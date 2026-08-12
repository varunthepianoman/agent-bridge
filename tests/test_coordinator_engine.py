from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agent_bridge_coordinator.engine import (
    AuthorityViolation,
    ConversationUnavailable,
    CoordinatorEngine,
)
from agent_bridge_coordinator.models import (
    ActivationSnapshot,
    BudgetUsage,
    CheckpointDraft,
    CoordinatorAction,
    CoordinatorActionType,
    CoordinatorModelOutput,
    CoordinatorSession,
    CoordinatorTurn,
)
from agent_bridge_protocol.models import (
    AuthorityLimits,
    AutonomyMode,
    CoordinatorRole,
    EndpointKind,
    EndpointRef,
    RoleCheckpoint,
    RoleLease,
    RoleReport,
    RoleStatus,
    WorkRequest,
)


def role(
    *,
    parent: str | None = None,
    conversation: str | None = "conversation-old",
    autonomy: AutonomyMode = AutonomyMode.DELEGATE,
) -> CoordinatorRole:
    return CoordinatorRole(
        role_id="role-work",
        role_type="work_coordinator",
        scope="work:work-1",
        charter="Coordinate work safely",
        authority_profile="delegate-bounded",
        parent_role_id=parent,
        current_conversation_id=conversation,
        autonomy_mode=autonomy,
        status=RoleStatus.ACTIVE,
    )


def request(
    mode: AutonomyMode,
    *,
    authority: AuthorityLimits | None = None,
) -> WorkRequest:
    return WorkRequest(
        request_id=f"request-{mode}",
        objective="Implement bounded change",
        mode=mode,
        requested_by=EndpointRef(kind=EndpointKind.ENDPOINT, id="user"),
        work_id="work-1",
        target_role_id="role-work",
        authority=authority or AuthorityLimits(),
    )


def output(*actions: CoordinatorAction) -> CoordinatorModelOutput:
    return CoordinatorModelOutput(
        checkpoint=CheckpointDraft(
            objective="Implement bounded change",
            status=RoleStatus.ACTIVE,
            decisions=["Keep scope bounded"],
            current_plan=["Execute validated actions"],
            parent_summary="Bounded work remains active",
        ),
        actions=list(actions),
    )


class FakeStore:
    def __init__(self, coordinator_role: CoordinatorRole | None = None) -> None:
        self.role = coordinator_role or role()
        self.activation_id = "activation-1"
        self.active_token = 7
        self.begins = 0
        self.bindings: list[CoordinatorSession] = []
        self.checkpoints: list[RoleCheckpoint] = []
        self.reports: list[RoleReport] = []
        self.usage: list[BudgetUsage] = []
        self.completed: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self.releases = 0
        self.renewals = 0
        self.assertions = 0
        self.on_commit: Any = None
        self.renew_error: Exception | None = None

    async def begin_activation(
        self, *_args: Any, request: WorkRequest, **_kwargs: Any
    ) -> ActivationSnapshot:
        self.begins += 1
        now = datetime.now(UTC)
        return ActivationSnapshot(
            activation_id=self.activation_id,
            role=self.role,
            lease=RoleLease(
                role_id=self.role.role_id,
                holder_id="engine",
                fencing_token=self.active_token,
                acquired_at=now,
                expires_at=now + timedelta(minutes=5),
            ),
            request=request,
            stale_child_role_ids=["role-stale-child"],
        )

    async def bind_conversation(
        self, _activation_id: str, session: CoordinatorSession
    ) -> CoordinatorSession:
        self.bindings.append(session)
        return session

    async def renew_activation(self, _activation_id: str, ttl_seconds: float) -> RoleLease:
        self.renewals += 1
        if self.renew_error is not None:
            raise self.renew_error
        now = datetime.now(UTC)
        return RoleLease(
            role_id=self.role.role_id,
            holder_id="engine",
            fencing_token=self.active_token,
            acquired_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )

    async def assert_activation_active(self, _activation_id: str) -> None:
        self.assertions += 1
        if self.active_token != 7:
            raise RuntimeError("stale fencing token")

    async def commit_checkpoint(self, _activation_id: str, checkpoint: RoleCheckpoint) -> None:
        if self.on_commit is not None:
            self.on_commit()
        if checkpoint.fencing_token != self.active_token:
            raise RuntimeError("stale fencing token")
        self.checkpoints.append(checkpoint)

    async def record_usage(self, _activation_id: str, usage: BudgetUsage) -> None:
        self.usage.append(usage)

    async def complete_activation(self, _activation_id: str, **values: Any) -> None:
        self.completed.append(values)

    async def fail_activation(self, _activation_id: str, error: str) -> None:
        self.failures.append(error)

    async def release_activation(self, _activation_id: str) -> None:
        self.releases += 1

    async def submit_child_report(self, report: RoleReport) -> None:
        self.reports.append(report)


class FakeModel:
    def __init__(
        self,
        model_output: CoordinatorModelOutput,
        *,
        fail_old_conversation: bool = False,
        usage: BudgetUsage | None = None,
        delay: float = 0,
    ) -> None:
        self.model_output = model_output
        self.fail_old_conversation = fail_old_conversation
        self.usage = usage or BudgetUsage(attempts=1, input_tokens=10, output_tokens=5)
        self.delay = delay
        self.prepares: list[bool] = []
        self.prompts: list[str] = []
        self.aborted: list[str] = []

    async def prepare(
        self,
        _role: CoordinatorRole,
        *,
        current_conversation_id: str | None,
        current_provider_thread_id: str | None,
        cwd: str | None,
        force_new: bool = False,
    ) -> CoordinatorSession:
        self.prepares.append(force_new)
        if force_new or current_conversation_id is None:
            return CoordinatorSession(
                conversation_id="conversation-new",
                is_replacement=True,
                handoff_summary="old conversation unavailable",
            )
        return CoordinatorSession(conversation_id=current_conversation_id)

    async def run(self, session: CoordinatorSession, prompt: str) -> CoordinatorTurn:
        self.prompts.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_old_conversation and session.conversation_id == "conversation-old":
            raise ConversationUnavailable("missing provider thread")
        return CoordinatorTurn(output=self.model_output, session=session, usage=self.usage)

    async def abort(self, session: CoordinatorSession) -> None:
        self.aborted.append(session.conversation_id)


class FakeExecutor:
    def __init__(self, store: FakeStore | None = None) -> None:
        self.actions: list[CoordinatorAction] = []
        self.store = store

    async def execute(self, action: CoordinatorAction, **_kwargs: Any) -> BudgetUsage:
        if self.store is not None:
            assert any(
                item.attempts >= action.attempt_count
                and item.total_tokens >= action.estimated_tokens
                and item.cost_usd >= action.estimated_cost_usd
                for item in self.store.usage
            ), "action authority must be durably reserved before dispatch"
        self.actions.append(action)
        return BudgetUsage(attempts=1, input_tokens=2, output_tokens=1, cost_usd=0.1)


def engine(
    store: FakeStore,
    model: FakeModel,
    executor: FakeExecutor | None = None,
) -> tuple[CoordinatorEngine, FakeExecutor]:
    resolved = executor or FakeExecutor()
    return (
        CoordinatorEngine(
            store=store,
            model=model,
            executor=resolved,
            holder_id="engine",
        ),
        resolved,
    )


async def test_manual_mode_bypasses_store_model_and_executor() -> None:
    store = FakeStore()
    coordinator, executor = engine(store, FakeModel(output()))
    result = await coordinator.activate("role-work", request(AutonomyMode.MANUAL))
    assert result.status == "awaiting_manual"
    assert store.begins == 0
    assert not executor.actions


async def test_advise_persists_checkpoint_but_waits_for_approval() -> None:
    action = CoordinatorAction(
        action_id="action-1",
        type=CoordinatorActionType.EXECUTE,
        summary="Run tests",
        target_id="node-a",
        capability="tests",
    )
    store = FakeStore()
    coordinator, executor = engine(store, FakeModel(output(action)))
    result = await coordinator.activate("role-work", request(AutonomyMode.ADVISE))
    assert result.status == "completed"
    assert result.attention_required == "Recommendations are awaiting user approval"
    assert not executor.actions
    assert store.checkpoints[0].fencing_token == 7


async def test_delegate_executes_bounded_capability_and_reports_to_parent() -> None:
    action = CoordinatorAction(
        action_id="action-1",
        type=CoordinatorActionType.EXECUTE,
        summary="Run tests",
        target_id="node-a",
        capability="tests",
        scope="work:work-1",
        estimated_tokens=7,
        estimated_cost_usd=0.1,
    )
    authority = AuthorityLimits(
        max_parallel_executions=1,
        max_attempts=2,
        token_budget=100,
        cost_budget_usd=1,
        allowed_capabilities=["tests"],
    )
    store = FakeStore(role(parent="role-portfolio"))
    coordinator, executor = engine(store, FakeModel(output(action)), FakeExecutor(store))
    result = await coordinator.activate(
        "role-work", request(AutonomyMode.DELEGATE, authority=authority)
    )
    assert result.executed_action_ids == ["action-1"]
    assert executor.actions == [action]
    assert sum(item.attempts for item in store.usage) == 2
    assert store.reports[0].checkpoint_version == result.checkpoint.version


async def test_approved_advise_actions_execute_only_as_the_exact_bounded_set() -> None:
    action = CoordinatorAction(
        action_id="approved-1",
        type=CoordinatorActionType.EXECUTE,
        summary="Run approved tests",
        target_id="node-a",
        capability="tests",
        scope="work:work-1",
        estimated_tokens=5,
    )
    authority = AuthorityLimits(
        max_parallel_executions=1,
        max_attempts=3,
        token_budget=100,
        allowed_capabilities=["tests"],
    )
    approved_request = request(AutonomyMode.DELEGATE, authority=authority).model_copy(
        update={
            "context": {
                "approved_action_ids": [action.action_id],
                "approved_actions": [action.model_dump(mode="json")],
            }
        }
    )
    store = FakeStore()
    coordinator, executor = engine(store, FakeModel(output(action)))
    result = await coordinator.activate("role-work", approved_request)
    assert result.executed_action_ids == [action.action_id]
    assert executor.actions == [action]

    changed = action.model_copy(update={"summary": "Changed after approval"})
    changed_store = FakeStore()
    changed_engine, _ = engine(changed_store, FakeModel(output(changed)))
    with pytest.raises(AuthorityViolation, match="changed after approval"):
        await changed_engine.activate("role-work", approved_request)


async def test_delegate_escalates_scope_expansion_without_execution() -> None:
    action = CoordinatorAction(
        action_id="expand-1",
        type=CoordinatorActionType.CREATE_ROLE,
        summary="Create a cross-work specialist",
        scope="work:other",
    )
    store = FakeStore()
    coordinator, executor = engine(store, FakeModel(output(action)))
    result = await coordinator.activate("role-work", request(AutonomyMode.DELEGATE))
    assert not executor.actions
    assert "Scope expansion" in (result.attention_required or "")


async def test_autonomous_fails_closed_without_explicit_limits() -> None:
    store = FakeStore()
    coordinator, _ = engine(store, FakeModel(output()))
    with pytest.raises(AuthorityViolation, match="token_budget or cost_budget"):
        await coordinator.activate("role-work", request(AutonomyMode.AUTONOMOUS))
    assert store.begins == 0


async def test_autonomous_executes_only_with_finite_authority() -> None:
    action = CoordinatorAction(
        action_id="execute-1",
        type=CoordinatorActionType.EXECUTE,
        summary="Run bounded validation",
        target_id="node-a",
        capability="tests",
        scope="work:work-1",
        estimated_tokens=10,
        estimated_cost_usd=0.1,
    )
    authority = AuthorityLimits(
        max_parallel_executions=1,
        max_attempts=4,
        token_budget=100,
        cost_budget_usd=2,
        deadline=datetime.now(UTC) + timedelta(hours=1),
        allowed_capabilities=["tests"],
        may_expand_scope=False,
    )
    store = FakeStore(role(autonomy=AutonomyMode.AUTONOMOUS))
    coordinator, executor = engine(store, FakeModel(output(action)))
    result = await coordinator.activate(
        "role-work", request(AutonomyMode.AUTONOMOUS, authority=authority)
    )
    assert result.executed_action_ids == ["execute-1"]
    assert executor.actions == [action]


async def test_unusable_conversation_rotates_with_handoff_and_recovers() -> None:
    store = FakeStore()
    model = FakeModel(output(), fail_old_conversation=True)
    coordinator, _ = engine(store, model)
    result = await coordinator.activate("role-work", request(AutonomyMode.ADVISE))
    assert model.prepares == [False, True]
    assert store.bindings[-1].conversation_id == "conversation-new"
    assert store.bindings[-1].handoff_summary
    assert result.conversation_id == "conversation-new"
    assert "role-stale-child" in model.prompts[-1]


async def test_stolen_fencing_token_rejects_checkpoint_after_model_turn() -> None:
    store = FakeStore()
    store.on_commit = lambda: setattr(store, "active_token", 8)
    coordinator, _ = engine(store, FakeModel(output()))
    with pytest.raises(RuntimeError, match="stale fencing"):
        await coordinator.activate("role-work", request(AutonomyMode.ADVISE))
    assert not store.checkpoints
    assert store.failures
    assert store.releases == 1


async def test_budget_overrun_fails_before_any_action() -> None:
    action = CoordinatorAction(
        action_id="action-1",
        type=CoordinatorActionType.EXECUTE,
        summary="Run tests",
        target_id="node-a",
        capability="tests",
    )
    authority = AuthorityLimits(
        max_attempts=3,
        token_budget=5,
        cost_budget_usd=1,
        allowed_capabilities=["tests"],
    )
    store = FakeStore()
    coordinator, executor = engine(
        store,
        FakeModel(output(action), usage=BudgetUsage(attempts=1, input_tokens=6)),
    )
    with pytest.raises(AuthorityViolation, match="token budget"):
        await coordinator.activate("role-work", request(AutonomyMode.DELEGATE, authority=authority))
    assert not executor.actions
    assert store.failures


async def test_cumulative_estimates_are_reserved_before_any_side_effect() -> None:
    actions = [
        CoordinatorAction(
            action_id=f"action-{index}",
            type=CoordinatorActionType.EXECUTE,
            summary="Run bounded work",
            target_id="node-a",
            capability="tests",
            scope="work:work-1",
            estimated_tokens=50,
        )
        for index in range(2)
    ]
    authority = AuthorityLimits(
        max_parallel_executions=2,
        max_attempts=5,
        token_budget=100,
        allowed_capabilities=["tests"],
    )
    store = FakeStore()
    coordinator, executor = engine(store, FakeModel(output(*actions)))
    with pytest.raises(AuthorityViolation, match="cumulative token"):
        await coordinator.activate("role-work", request(AutonomyMode.DELEGATE, authority=authority))
    assert not executor.actions


async def test_lease_renewal_failure_cancels_long_model_turn() -> None:
    store = FakeStore()
    store.renew_error = RuntimeError("lease stolen")
    coordinator = CoordinatorEngine(
        store=store,
        model=FakeModel(output(), delay=1),
        executor=FakeExecutor(),
        holder_id="engine",
        lease_seconds=0.15,
    )
    with pytest.raises(RuntimeError, match="lease stolen"):
        await coordinator.activate("role-work", request(AutonomyMode.ADVISE))
    assert store.renewals == 1
    assert not store.checkpoints
