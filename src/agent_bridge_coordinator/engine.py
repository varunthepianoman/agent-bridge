"""Coordinator activation, authority policy, hierarchy reporting, and recovery."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeVar
from uuid import uuid4

from agent_bridge_protocol.models import (
    AutonomyMode,
    CoordinatorRole,
    RoleCheckpoint,
    RoleLease,
    RoleReport,
    WorkRequest,
)

from .models import (
    ActivationSnapshot,
    BudgetUsage,
    CoordinatorAction,
    CoordinatorActionType,
    CoordinatorActivationResult,
    CoordinatorModelOutput,
    CoordinatorSession,
    CoordinatorTurn,
)


class AuthorityViolation(RuntimeError):
    pass


class ConversationUnavailable(RuntimeError):
    pass


class CoordinatorStore(Protocol):
    async def begin_activation(
        self,
        role_id: str,
        holder_id: str,
        *,
        request: WorkRequest,
        intake_id: str | None,
        ttl_seconds: float,
    ) -> ActivationSnapshot: ...

    async def bind_conversation(
        self, activation_id: str, session: CoordinatorSession
    ) -> CoordinatorSession: ...

    async def renew_activation(self, activation_id: str, ttl_seconds: float) -> RoleLease: ...

    async def assert_activation_active(self, activation_id: str) -> None: ...

    async def commit_checkpoint(self, activation_id: str, checkpoint: RoleCheckpoint) -> None: ...

    async def record_usage(self, activation_id: str, usage: BudgetUsage) -> None: ...

    async def complete_activation(
        self,
        activation_id: str,
        *,
        conversation_id: str,
        proposed_actions: list[CoordinatorAction],
        executed_action_ids: list[str],
        attention_required: str | None,
    ) -> None: ...

    async def fail_activation(self, activation_id: str, error: str) -> None: ...

    async def release_activation(self, activation_id: str) -> None: ...

    async def submit_child_report(self, report: RoleReport) -> None: ...


class CoordinatorModel(Protocol):
    async def prepare(
        self,
        role: CoordinatorRole,
        *,
        current_conversation_id: str | None,
        current_provider_thread_id: str | None,
        cwd: str | None,
        force_new: bool = False,
    ) -> CoordinatorSession: ...

    async def run(self, session: CoordinatorSession, prompt: str) -> CoordinatorTurn: ...

    async def abort(self, session: CoordinatorSession) -> None: ...


class CoordinatorActionExecutor(Protocol):
    async def execute(
        self,
        action: CoordinatorAction,
        *,
        request: WorkRequest,
        role: CoordinatorRole,
    ) -> BudgetUsage: ...


T = TypeVar("T")


class CoordinatorEngine:
    def __init__(
        self,
        *,
        store: CoordinatorStore,
        model: CoordinatorModel,
        executor: CoordinatorActionExecutor,
        holder_id: str,
        lease_seconds: float = 300,
    ) -> None:
        self.store = store
        self.model = model
        self.executor = executor
        self.holder_id = holder_id
        self.lease_seconds = lease_seconds

    async def activate(
        self,
        role_id: str,
        request: WorkRequest,
        *,
        intake_id: str | None = None,
    ) -> CoordinatorActivationResult:
        mode = AutonomyMode(request.mode)
        if mode == AutonomyMode.MANUAL:
            return CoordinatorActivationResult(
                role_id=role_id,
                mode=mode,
                status="awaiting_manual",
                attention_required="Manual mode bypasses coordinator inference and routing",
                completed_at=datetime.now(UTC),
            )
        self._validate_activation_authority(request)
        snapshot = await self.store.begin_activation(
            role_id,
            self.holder_id,
            request=request,
            intake_id=intake_id,
            ttl_seconds=self.lease_seconds,
        )
        session: CoordinatorSession | None = None
        heartbeat = asyncio.create_task(self._renew_loop(snapshot.activation_id))
        try:
            self._validate_role_scope(snapshot.role, request)
            self._validate_remaining_budget(request, snapshot.usage)
            session = await self.model.prepare(
                snapshot.role,
                current_conversation_id=snapshot.role.current_conversation_id,
                current_provider_thread_id=snapshot.provider_thread_id,
                cwd=snapshot.workspace,
            )
            if (
                session.is_replacement
                or session.conversation_id != snapshot.role.current_conversation_id
            ):
                session = await self.store.bind_conversation(snapshot.activation_id, session)
            prompt = self._assemble_context(snapshot)
            try:
                turn = await self._with_heartbeat(self.model.run(session, prompt), heartbeat)
            except ConversationUnavailable:
                replacement = await self.model.prepare(
                    snapshot.role,
                    current_conversation_id=snapshot.role.current_conversation_id,
                    current_provider_thread_id=snapshot.provider_thread_id,
                    cwd=snapshot.workspace,
                    force_new=True,
                )
                session = await self.store.bind_conversation(snapshot.activation_id, replacement)
                turn = await self._with_heartbeat(self.model.run(session, prompt), heartbeat)
            if turn.session.conversation_id != session.conversation_id:
                turn = turn.model_copy(
                    update={
                        "session": await self.store.bind_conversation(
                            snapshot.activation_id, turn.session
                        )
                    }
                )
            await self.store.record_usage(snapshot.activation_id, turn.usage)
            self._raise_heartbeat_failure(heartbeat)
            await self.store.assert_activation_active(snapshot.activation_id)
            total_usage = self._add_usage(snapshot.usage, turn.usage)
            self._validate_remaining_budget(request, total_usage)
            self._validate_output(snapshot, turn.output)
            authorized, attention = self._authorize_actions(
                request, snapshot.role, turn.output, total_usage
            )
            reservation = self._action_reservation(authorized)
            if reservation.attempts or reservation.total_tokens or reservation.cost_usd:
                # Persist the complete reservation while this activation still owns the
                # fence. A crash after the first dispatch therefore cannot recover with
                # the original, unconsumed budget and expand the authorized action set.
                await self.store.assert_activation_active(snapshot.activation_id)
                await self.store.record_usage(snapshot.activation_id, reservation)
                total_usage = self._add_usage(total_usage, reservation)
                self._validate_remaining_budget(request, total_usage)
            executed: list[str] = []
            for action in authorized:
                self._raise_heartbeat_failure(heartbeat)
                await self.store.assert_activation_active(snapshot.activation_id)
                usage = await self._with_heartbeat(
                    self.executor.execute(
                        action,
                        request=request,
                        role=snapshot.role,
                    ),
                    heartbeat,
                )
                await self.store.assert_activation_active(snapshot.activation_id)
                # The estimate was consumed durably before dispatch. Settle only an
                # overage, retaining the reservation when actual usage is lower so a
                # restart cannot reclaim already-authorized capacity.
                usage_delta = self._usage_over_reservation(action, usage)
                total_usage = self._add_usage(total_usage, usage_delta)
                if usage_delta.attempts or usage_delta.total_tokens or usage_delta.cost_usd:
                    await self.store.record_usage(snapshot.activation_id, usage_delta)
                self._validate_remaining_budget(request, total_usage)
                executed.append(action.action_id)
            checkpoint = self._checkpoint(snapshot, turn.output)
            # Store commit must transactionally revalidate lease fencing after the model turn.
            await self.store.commit_checkpoint(snapshot.activation_id, checkpoint)
            if snapshot.role.parent_role_id is not None:
                await self.store.submit_child_report(self._parent_report(snapshot.role, checkpoint))
            attention_required = attention or turn.output.attention_required
            await self.store.complete_activation(
                snapshot.activation_id,
                conversation_id=turn.session.conversation_id,
                proposed_actions=turn.output.actions,
                executed_action_ids=executed,
                attention_required=attention_required,
            )
            return CoordinatorActivationResult(
                activation_id=snapshot.activation_id,
                role_id=role_id,
                mode=mode,
                status="completed",
                checkpoint=checkpoint,
                proposed_actions=turn.output.actions,
                executed_action_ids=executed,
                attention_required=attention_required,
                conversation_id=turn.session.conversation_id,
                completed_at=datetime.now(UTC),
            )
        except Exception as error:
            with contextlib.suppress(Exception):
                await self.store.fail_activation(snapshot.activation_id, str(error))
            raise
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await heartbeat
            try:
                if session is not None:
                    with contextlib.suppress(Exception):
                        await self.model.abort(session)
            finally:
                with contextlib.suppress(Exception):
                    await self.store.release_activation(snapshot.activation_id)

    async def _renew_loop(self, activation_id: str) -> None:
        interval = max(0.05, self.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            await self.store.renew_activation(activation_id, self.lease_seconds)

    @staticmethod
    def _raise_heartbeat_failure(heartbeat: asyncio.Task[None]) -> None:
        if heartbeat.done():
            heartbeat.result()
            raise RuntimeError("coordinator lease renewal stopped unexpectedly")

    @staticmethod
    async def _with_heartbeat(awaitable: Awaitable[T], heartbeat: asyncio.Task[None]) -> T:
        operation = asyncio.ensure_future(awaitable)
        done, _ = await asyncio.wait({operation, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
        if heartbeat in done:
            operation.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await operation
            heartbeat.result()
            raise RuntimeError("coordinator lease renewal stopped unexpectedly")
        return operation.result()

    @staticmethod
    def _validate_activation_authority(request: WorkRequest) -> None:
        if request.mode != AutonomyMode.AUTONOMOUS:
            return
        authority = request.authority
        missing: list[str] = []
        if authority.token_budget is None and authority.cost_budget_usd is None:
            missing.append("token_budget or cost_budget_usd")
        if authority.deadline is None:
            missing.append("deadline")
        if not authority.allowed_capabilities:
            missing.append("allowed_capabilities")
        if request.work_id is None and request.target_role_id is None:
            missing.append("work_id or target_role_id scope")
        if missing:
            raise AuthorityViolation("autonomous mode requires explicit " + ", ".join(missing))

    @staticmethod
    def _validate_remaining_budget(request: WorkRequest, usage: BudgetUsage) -> None:
        authority = request.authority
        if authority.deadline is not None:
            deadline = authority.deadline
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            if datetime.now(UTC) >= deadline:
                raise AuthorityViolation("authority deadline has expired")
        if usage.attempts > authority.max_attempts:
            raise AuthorityViolation("attempt budget exhausted")
        if authority.token_budget is not None and usage.total_tokens > authority.token_budget:
            raise AuthorityViolation("token budget exceeded")
        if (
            authority.cost_budget_usd is not None
            and authority.token_budget is None
            and not usage.cost_known
        ):
            raise AuthorityViolation("cost-only budget cannot be enforced for unknown model cost")
        if authority.cost_budget_usd is not None and usage.cost_usd > authority.cost_budget_usd:
            raise AuthorityViolation("cost budget exceeded")

    @staticmethod
    def _validate_role_scope(role: CoordinatorRole, request: WorkRequest) -> None:
        if request.target_role_id is not None and request.target_role_id != role.role_id:
            raise AuthorityViolation("activation role does not match target_role_id")
        if (
            request.work_id is not None
            and role.role_type != "portfolio_coordinator"
            and role.scope != f"work:{request.work_id}"
        ):
            raise AuthorityViolation("activation role is outside requested work scope")
        levels = {
            AutonomyMode.MANUAL: 0,
            AutonomyMode.ADVISE: 1,
            AutonomyMode.DELEGATE: 2,
            AutonomyMode.AUTONOMOUS: 3,
        }
        if levels[AutonomyMode(request.mode)] > levels[AutonomyMode(role.autonomy_mode)]:
            raise AuthorityViolation("request mode exceeds the role autonomy ceiling")
        if (
            request.work_id is not None
            and request.authority.allowed_work_ids
            and request.work_id not in request.authority.allowed_work_ids
        ):
            raise AuthorityViolation("requested work is outside allowed_work_ids")

    def _authorize_actions(
        self,
        request: WorkRequest,
        role: CoordinatorRole,
        output: CoordinatorModelOutput,
        usage: BudgetUsage,
    ) -> tuple[list[CoordinatorAction], str | None]:
        mode = AutonomyMode(request.mode)
        if mode == AutonomyMode.ADVISE:
            if output.checkpoint.active_delegations:
                raise AuthorityViolation("advise mode cannot publish active delegations")
            return [], "Recommendations are awaiting user approval"
        executable = [
            action for action in output.actions if action.type != CoordinatorActionType.RECOMMEND
        ]
        if "approved_action_ids" in request.context:
            approved_ids = {str(item) for item in request.context.get("approved_action_ids", [])}
            proposed_ids = {action.action_id for action in executable}
            if proposed_ids != approved_ids:
                raise AuthorityViolation(
                    "delegate output does not match the explicitly approved action set"
                )
            approved_actions = {
                str(item.get("action_id")): item
                for item in request.context.get("approved_actions", [])
                if isinstance(item, dict) and item.get("action_id")
            }
            for action in executable:
                if action.model_dump(mode="json") != approved_actions.get(action.action_id):
                    raise AuthorityViolation(
                        f"approved action {action.action_id} changed after approval"
                    )
        if len(executable) > request.authority.max_parallel_executions:
            raise AuthorityViolation("action count exceeds max_parallel_executions")
        reserved_attempts = usage.attempts + sum(action.attempt_count for action in executable)
        reserved_tokens = usage.total_tokens + sum(action.estimated_tokens for action in executable)
        reserved_cost = usage.cost_usd + sum(action.estimated_cost_usd for action in executable)
        if reserved_attempts > request.authority.max_attempts:
            raise AuthorityViolation("proposed actions exceed cumulative attempt authority")
        if (
            request.authority.token_budget is not None
            and reserved_tokens > request.authority.token_budget
        ):
            raise AuthorityViolation("proposed actions exceed cumulative token budget")
        if (
            request.authority.cost_budget_usd is not None
            and reserved_cost > request.authority.cost_budget_usd
        ):
            raise AuthorityViolation("proposed actions exceed cumulative cost budget")
        for action in executable:
            if action.attempt_count + usage.attempts > request.authority.max_attempts:
                raise AuthorityViolation(f"action {action.action_id} exceeds retry authority")
            if action.estimated_tokens + usage.total_tokens > (
                request.authority.token_budget or float("inf")
            ):
                raise AuthorityViolation(f"action {action.action_id} exceeds token budget")
            if action.estimated_cost_usd + usage.cost_usd > (
                request.authority.cost_budget_usd or float("inf")
            ):
                raise AuthorityViolation(f"action {action.action_id} exceeds cost budget")
            if action.type in {
                CoordinatorActionType.EXECUTE,
                CoordinatorActionType.DELEGATE,
                CoordinatorActionType.RETRY,
            }:
                if action.target_id is None:
                    raise AuthorityViolation(f"action {action.action_id} has no target")
                if action.capability is None:
                    raise AuthorityViolation(f"action {action.action_id} has no capability")
                if action.capability not in request.authority.allowed_capabilities:
                    raise AuthorityViolation(
                        f"capability {action.capability!r} is outside authority"
                    )
            elif (
                action.capability is not None
                and action.capability not in request.authority.allowed_capabilities
            ):
                raise AuthorityViolation(f"capability {action.capability!r} is outside authority")
            if action.scope is None:
                raise AuthorityViolation(f"action {action.action_id} has no explicit scope")
            self._validate_action_resources(request, action)
            expands_scope = action.expands_scope or action.type in {
                CoordinatorActionType.CREATE_WORK,
                CoordinatorActionType.CREATE_ROLE,
            }
            if expands_scope and not request.authority.may_expand_scope:
                if mode == AutonomyMode.DELEGATE:
                    return [], f"Scope expansion in {action.action_id} requires user approval"
                raise AuthorityViolation(f"action {action.action_id} exceeds scope authority")
            if (
                action.scope is not None
                and action.scope != role.scope
                and not request.authority.may_expand_scope
            ):
                raise AuthorityViolation(f"action {action.action_id} targets another scope")
        return executable, None

    @staticmethod
    def _validate_output(snapshot: ActivationSnapshot, output: CoordinatorModelOutput) -> None:
        if output.checkpoint.objective != snapshot.request.objective:
            raise AuthorityViolation("coordinator output changed the requested objective")

    @staticmethod
    def _validate_action_resources(request: WorkRequest, action: CoordinatorAction) -> None:
        authority = request.authority
        action_work = action.payload.get("work_id", request.work_id)
        if authority.allowed_work_ids and action_work not in authority.allowed_work_ids:
            raise AuthorityViolation(f"action {action.action_id} targets disallowed work")
        repository = action.payload.get("repository_id")
        if repository is not None and not isinstance(repository, str):
            raise AuthorityViolation(f"action {action.action_id} has invalid repository_id")
        if (
            repository is not None
            and authority.allowed_repository_ids
            and repository not in authority.allowed_repository_ids
        ):
            raise AuthorityViolation(f"action {action.action_id} targets disallowed repository")
        path_value = action.payload.get("path")
        if path_value is not None:
            if not isinstance(path_value, str) or not authority.allowed_paths:
                raise AuthorityViolation(f"action {action.action_id} has unauthorized path")
            candidate = Path(path_value).resolve()
            roots = [Path(item).resolve() for item in authority.allowed_paths]
            if not any(candidate == root or root in candidate.parents for root in roots):
                raise AuthorityViolation(f"action {action.action_id} path is outside authority")

    @staticmethod
    def _add_usage(left: BudgetUsage, right: BudgetUsage) -> BudgetUsage:
        return BudgetUsage(
            attempts=left.attempts + right.attempts,
            input_tokens=left.input_tokens + right.input_tokens,
            output_tokens=left.output_tokens + right.output_tokens,
            cost_usd=left.cost_usd + right.cost_usd,
            cost_known=left.cost_known and right.cost_known,
        )

    @staticmethod
    def _action_reservation(actions: list[CoordinatorAction]) -> BudgetUsage:
        return BudgetUsage(
            attempts=sum(action.attempt_count for action in actions),
            input_tokens=sum(action.estimated_tokens for action in actions),
            cost_usd=sum(action.estimated_cost_usd for action in actions),
        )

    @staticmethod
    def _usage_over_reservation(action: CoordinatorAction, actual: BudgetUsage) -> BudgetUsage:
        return BudgetUsage(
            attempts=max(0, actual.attempts - action.attempt_count),
            input_tokens=max(0, actual.total_tokens - action.estimated_tokens),
            cost_usd=max(0, actual.cost_usd - action.estimated_cost_usd),
            cost_known=actual.cost_known,
        )

    @staticmethod
    def _checkpoint(snapshot: ActivationSnapshot, output: CoordinatorModelOutput) -> RoleCheckpoint:
        draft = output.checkpoint
        return RoleCheckpoint(
            role_id=snapshot.role.role_id,
            version=snapshot.role.checkpoint_version + 1,
            fencing_token=snapshot.lease.fencing_token,
            objective=draft.objective,
            charter=snapshot.role.charter,
            authority_profile=snapshot.role.authority_profile,
            status=draft.status,
            decisions=draft.decisions,
            completed_delegations=draft.completed_delegations,
            active_delegations=draft.active_delegations,
            open_questions=draft.open_questions,
            blockers=draft.blockers,
            dependencies=draft.dependencies,
            evidence=draft.evidence,
            current_plan=draft.current_plan,
            recommended_next_action=draft.recommended_next_action,
            parent_summary=draft.parent_summary,
        )

    @staticmethod
    def _parent_report(role: CoordinatorRole, checkpoint: RoleCheckpoint) -> RoleReport:
        assert role.parent_role_id is not None
        return RoleReport(
            report_id=f"report-{uuid4().hex}",
            reporting_role_id=role.role_id,
            recipient_role_id=role.parent_role_id,
            checkpoint_version=checkpoint.version,
            status=checkpoint.status,
            summary=checkpoint.parent_summary,
            decisions=checkpoint.decisions,
            evidence=checkpoint.evidence,
            attention_required=(checkpoint.blockers[0] if checkpoint.blockers else None),
            recommended_action=checkpoint.recommended_next_action,
        )

    @staticmethod
    def _assemble_context(snapshot: ActivationSnapshot) -> str:
        context = {
            "role": snapshot.role.model_dump(mode="json"),
            "request": snapshot.request.model_dump(mode="json"),
            "latest_checkpoint": (
                snapshot.latest_checkpoint.model_dump(mode="json")
                if snapshot.latest_checkpoint
                else None
            ),
            "received_reports": [
                report.model_dump(mode="json") for report in snapshot.received_reports
            ],
            "child_reports": [report.model_dump(mode="json") for report in snapshot.child_reports],
            "stale_child_role_ids": snapshot.stale_child_role_ids,
            "conversation_history": snapshot.conversation_history,
            "context": snapshot.context,
        }
        return (
            "Act as the durable coordinator role described below. Return only the strict "
            "structured response requested by the supplied JSON Schema. Preserve charter and "
            "authority; surface uncertainty and meaningful scope expansion for approval.\n"
            + json.dumps(context, sort_keys=True, separators=(",", ":"), default=str)
        )
