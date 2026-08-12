from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_bridge_protocol.models import (
    AuthorityLimits,
    AuthorityUsage,
    AutonomyMode,
    CoordinatorActivation,
    CoordinatorActivationStatus,
    CoordinatorIntake,
    CoordinatorIntakeStatus,
    CoordinatorRole,
    RoleCheckpoint,
    RoleReport,
    RoleRollupState,
    RoleStatus,
    WorkRequest,
)

from .db import (
    ConversationRow,
    CoordinatorActivationRow,
    CoordinatorIntakeEventRow,
    CoordinatorIntakeRow,
    CoordinatorRoleRow,
    Database,
    RoleLeaseRow,
    RoleReportRow,
    RoleRollupStateRow,
)
from .roles import ConflictError, NotFoundError, RoleStore, StaleFencingTokenError

_TERMINAL_INTAKE = {"completed", "failed", "rejected"}


class AuthorityLimitError(ConflictError):
    pass


class CoordinatorStore:
    """Durable coordinator control plane, independent from any model runtime."""

    def __init__(self, database: Database, role_store: RoleStore) -> None:
        self.database = database
        self.roles = role_store

    # Portfolio intake -------------------------------------------------
    def create_intake(self, work_request: WorkRequest) -> CoordinatorIntake:
        if work_request.mode == AutonomyMode.MANUAL:
            raise ConflictError("manual mode bypasses coordinator intake; use /bridge/requests")
        self._validate_authority(work_request.mode, work_request.authority, work_request)
        now = _now()
        intake = CoordinatorIntake(
            request_id=work_request.request_id,
            request=work_request,
            status=CoordinatorIntakeStatus.SUBMITTED,
            routed_work_id=work_request.work_id,
            routed_role_id=work_request.target_role_id,
            approval_required=False,
            created_at=now,
            updated_at=now,
        )
        with self.database.session() as session:
            if session.get(CoordinatorIntakeRow, intake.request_id):
                raise ConflictError(f"intake already exists: {intake.request_id}")
            if (
                intake.routed_role_id
                and session.get(CoordinatorRoleRow, intake.routed_role_id) is None
            ):
                raise NotFoundError(f"unknown role: {intake.routed_role_id}")
            session.add(_intake_row(intake))
            session.flush()
            self._append_intake_event(session, intake.request_id, "intake.submitted", {})
            session.commit()
        return intake

    def get_intake(self, request_id: str) -> CoordinatorIntake | None:
        with self.database.session() as session:
            row = session.get(CoordinatorIntakeRow, request_id)
            return _intake_model(row) if row else None

    def list_intakes(
        self,
        *,
        status: str | None = None,
        mode: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[CoordinatorIntake], int]:
        filters = []
        if status:
            filters.append(CoordinatorIntakeRow.status == status)
        if mode:
            filters.append(CoordinatorIntakeRow.mode == mode)
        with self.database.session() as session:
            total = (
                session.scalar(
                    select(func.count()).select_from(CoordinatorIntakeRow).where(*filters)
                )
                or 0
            )
            rows = session.scalars(
                select(CoordinatorIntakeRow)
                .where(*filters)
                .order_by(CoordinatorIntakeRow.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [_intake_model(row) for row in rows], int(total)

    def update_intake(
        self,
        request_id: str,
        *,
        status: CoordinatorIntakeStatus | None = None,
        routed_work_id: str | None = None,
        routed_role_id: str | None = None,
        proposed_actions: list[dict[str, Any]] | None = None,
        proposed_topology: dict[str, Any] | None = None,
        attention_required: str | None = None,
        approval_required: bool | None = None,
        executed: bool | None = None,
    ) -> CoordinatorIntake:
        now = _now()
        with self.database.session() as session:
            row = session.get(CoordinatorIntakeRow, request_id)
            if row is None:
                raise NotFoundError(f"unknown intake: {request_id}")
            if row.status in _TERMINAL_INTAKE:
                raise ConflictError(f"terminal intake cannot be updated: {row.status}")
            if routed_role_id and session.get(CoordinatorRoleRow, routed_role_id) is None:
                raise NotFoundError(f"unknown role: {routed_role_id}")
            if status is not None:
                row.status = str(status)
            if routed_work_id is not None:
                row.routed_work_id = routed_work_id
            if routed_role_id is not None:
                row.routed_role_id = routed_role_id
            if proposed_actions is not None:
                row.proposed_actions_json = _json(proposed_actions)
            if proposed_topology is not None:
                row.proposed_topology_json = _json(proposed_topology)
            if attention_required is not None:
                row.attention_required = attention_required
            if approval_required is not None:
                row.approval_required = approval_required
            if executed is not None:
                row.executed = executed
            row.updated_at = now
            self._append_intake_event(
                session,
                request_id,
                "intake.updated",
                {"status": row.status, "routed_role_id": row.routed_role_id},
            )
            session.commit()
            return _intake_model(row)

    def decide_intake(
        self,
        request_id: str,
        *,
        approved: bool,
        note: str | None = None,
        authority: AuthorityLimits | None = None,
    ) -> CoordinatorIntake:
        now = _now()
        with self.database.session() as session:
            row = session.get(CoordinatorIntakeRow, request_id)
            if row is None:
                raise NotFoundError(f"unknown intake: {request_id}")
            if row.status in _TERMINAL_INTAKE:
                raise ConflictError(f"terminal intake cannot be decided: {row.status}")
            if row.status != CoordinatorIntakeStatus.AWAITING_APPROVAL:
                raise ConflictError("intake has no pending approval decision")
            request = WorkRequest.model_validate_json(row.request_json)
            if authority is not None:
                request = request.model_copy(update={"authority": authority})
                self._validate_authority(request.mode, authority, request)
            if approved and request.mode == AutonomyMode.ADVISE:
                actions = json.loads(row.proposed_actions_json)
                executable = [
                    item
                    for item in actions
                    if isinstance(item, dict)
                    and item.get("type") in {"execute", "delegate", "retry"}
                ]
                if executable and authority is None:
                    raise ValueError(
                        "approving advise execution requires explicit bounded authority"
                    )
                if executable:
                    assert authority is not None
                    self._validate_approved_actions(executable, authority)
                request = request.model_copy(
                    update={
                        "mode": AutonomyMode.DELEGATE,
                        "context": {
                            **request.context,
                            "approved_action_ids": [str(item["action_id"]) for item in executable],
                            "approved_actions": executable,
                            "approval_source": request_id,
                        },
                    }
                )
                row.mode = str(AutonomyMode.DELEGATE)
            row.request_json = request.model_dump_json()
            row.status = "approved" if approved else "rejected"
            row.approval_required = False
            row.attention_required = None
            row.decision_note = note
            row.updated_at = now
            self._append_intake_event(
                session,
                request_id,
                "intake.approved" if approved else "intake.rejected",
                {"note": note},
            )
            session.commit()
            return _intake_model(row)

    def list_intake_events(self, request_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            if session.get(CoordinatorIntakeRow, request_id) is None:
                raise NotFoundError(f"unknown intake: {request_id}")
            rows = session.scalars(
                select(CoordinatorIntakeEventRow)
                .where(CoordinatorIntakeEventRow.request_id == request_id)
                .order_by(CoordinatorIntakeEventRow.sequence)
            ).all()
            return [
                {
                    "event_id": row.event_id,
                    "request_id": row.request_id,
                    "sequence": row.sequence,
                    "type": row.type,
                    "data": json.loads(row.data_json),
                    "occurred_at": _iso(row.occurred_at),
                }
                for row in rows
            ]

    # Activation -------------------------------------------------------
    def begin_activation(
        self,
        role_id: str,
        *,
        holder_id: str,
        intake_request_id: str | None = None,
        ttl_seconds: float = 300,
        authority: AuthorityLimits | None = None,
    ) -> dict[str, Any]:
        self.expire_stale_activations()
        role = self.roles.get_role(role_id)
        if role is None:
            raise NotFoundError(f"unknown role: {role_id}")
        intake = self.get_intake(intake_request_id) if intake_request_id else None
        if intake_request_id and intake is None:
            raise NotFoundError(f"unknown intake: {intake_request_id}")
        if intake:
            levels = {
                AutonomyMode.MANUAL: 0,
                AutonomyMode.ADVISE: 1,
                AutonomyMode.DELEGATE: 2,
                AutonomyMode.AUTONOMOUS: 3,
            }
            if levels[AutonomyMode(intake.request.mode)] > levels[AutonomyMode(role.autonomy_mode)]:
                raise AuthorityLimitError("request mode exceeds role autonomy ceiling")
            if intake.status in {
                CoordinatorIntakeStatus.ACTIVE,
                CoordinatorIntakeStatus.COMPLETED,
                CoordinatorIntakeStatus.FAILED,
                CoordinatorIntakeStatus.REJECTED,
            }:
                raise ConflictError(f"intake cannot be activated from {intake.status} status")
            if intake.request.target_role_id and intake.request.target_role_id != role_id:
                raise AuthorityLimitError("activation role is outside requested target role")
            if intake.routed_role_id and intake.routed_role_id != role_id:
                raise AuthorityLimitError("activation role is outside routed role")
            if (
                intake.request.work_id
                and role.role_type != "portfolio_coordinator"
                and role.scope != f"work:{intake.request.work_id}"
            ):
                raise AuthorityLimitError("activation role is outside requested work scope")
        effective_authority = authority or (
            intake.request.authority if intake else AuthorityLimits()
        )
        if intake and authority:
            self._validate_not_broader(authority, intake.request.authority)
        self._validate_authority(
            intake.request.mode if intake else AutonomyMode.DELEGATE,
            effective_authority,
            intake.request if intake else None,
        )
        if intake and intake.approval_required:
            raise ConflictError("intake requires approval before activation")
        if intake and intake.status == CoordinatorIntakeStatus.REJECTED:
            raise ConflictError("rejected intake cannot be activated")
        with self.database.session() as session:
            duplicate = session.scalar(
                select(CoordinatorActivationRow).where(
                    CoordinatorActivationRow.role_id == role_id,
                    CoordinatorActivationRow.status == CoordinatorActivationStatus.ACTIVE,
                )
            )
            if duplicate is not None:
                raise ConflictError(
                    f"role already has active activation: {duplicate.activation_id}"
                )
        lease = self.roles.acquire_role_lease(role_id, holder_id, ttl_seconds)
        now = _now()
        context = self.assemble_context(role_id, intake_request_id=intake_request_id)
        activation = CoordinatorActivation(
            activation_id=f"activation-{uuid4().hex}",
            role_id=role_id,
            intake_request_id=intake_request_id,
            holder_id=holder_id,
            fencing_token=lease.fencing_token,
            checkpoint_version_before=role.checkpoint_version,
            conversation_id=role.current_conversation_id,
            authority=effective_authority,
            started_at=now,
            updated_at=now,
        )
        try:
            with self.database.session() as session:
                session.add(
                    CoordinatorActivationRow(
                        activation_id=activation.activation_id,
                        role_id=role_id,
                        intake_request_id=intake_request_id,
                        holder_id=holder_id,
                        fencing_token=lease.fencing_token,
                        status=str(activation.status),
                        checkpoint_version_before=role.checkpoint_version,
                        conversation_id=role.current_conversation_id,
                        authority_json=effective_authority.model_dump_json(),
                        usage_json=activation.usage.model_dump_json(),
                        context_json=_json(context),
                        started_at=now,
                        updated_at=now,
                    )
                )
                session.commit()
        except Exception:
            self.roles.release_role_lease(role_id, holder_id, lease.fencing_token)
            raise
        if intake:
            self.update_intake(
                intake.request_id,
                status=CoordinatorIntakeStatus.ACTIVE,
                routed_role_id=role_id,
            )
        return {
            "activation": activation.model_dump(mode="json"),
            "lease": lease.model_dump(mode="json"),
            "context": context,
        }

    def get_activation(self, activation_id: str) -> CoordinatorActivation | None:
        with self.database.session() as session:
            row = session.get(CoordinatorActivationRow, activation_id)
            return _activation_model(row) if row else None

    def list_activations(
        self, *, role_id: str | None = None, status: str | None = None
    ) -> list[CoordinatorActivation]:
        self.expire_stale_activations()
        with self.database.session() as session:
            statement = select(CoordinatorActivationRow)
            if role_id:
                statement = statement.where(CoordinatorActivationRow.role_id == role_id)
            if status:
                statement = statement.where(CoordinatorActivationRow.status == status)
            statement = statement.order_by(CoordinatorActivationRow.started_at.desc())
            return [_activation_model(row) for row in session.scalars(statement)]

    def set_activation_conversation(
        self, activation_id: str, conversation_id: str
    ) -> CoordinatorActivation:
        with self.database.session() as session:
            row = self._require_active_activation(session, activation_id)
            self._validate_activation_lease(session, row)
            row.conversation_id = conversation_id
            row.updated_at = _now()
            session.commit()
            return _activation_model(row)

    def renew_activation(self, activation_id: str, ttl_seconds: float) -> Any:
        activation = self.get_activation(activation_id)
        if activation is None or activation.status != CoordinatorActivationStatus.ACTIVE:
            raise ConflictError("activation is not active")
        lease = self.roles.renew_role_lease(
            activation.role_id,
            activation.holder_id,
            activation.fencing_token,
            ttl_seconds,
        )
        with self.database.session() as session:
            row = self._require_active_activation(session, activation_id)
            self._validate_activation_lease(session, row)
            row.updated_at = _now()
            session.commit()
        return lease

    def assert_activation_active(self, activation_id: str) -> None:
        with self.database.session() as session:
            activation = self._require_active_activation(session, activation_id)
            self._validate_activation_lease(session, activation)

    def record_usage(
        self,
        activation_id: str,
        *,
        tokens: int = 0,
        cost_usd: float = 0,
        attempts: int = 0,
        active_executions_delta: int = 0,
        total_executions: int = 0,
    ) -> CoordinatorActivation:
        if tokens < 0 or cost_usd < 0 or attempts < 0 or total_executions < 0:
            raise ValueError("usage increments cannot be negative")
        with self.database.session() as session:
            row = self._require_active_activation(session, activation_id)
            authority = AuthorityLimits.model_validate_json(row.authority_json)
            usage = AuthorityUsage.model_validate_json(row.usage_json)
            updated = AuthorityUsage(
                tokens_used=usage.tokens_used + tokens,
                cost_used_usd=usage.cost_used_usd + cost_usd,
                attempts_used=usage.attempts_used + attempts,
                active_executions=usage.active_executions + active_executions_delta,
                total_executions=usage.total_executions + total_executions,
            )
            self._enforce_usage(authority, updated)
            row.usage_json = updated.model_dump_json()
            row.updated_at = _now()
            session.commit()
            return _activation_model(row)

    def authorize_action(
        self,
        activation_id: str,
        *,
        capability: str,
        work_id: str | None = None,
        repository_id: str | None = None,
        path: str | None = None,
        expand_scope: bool = False,
    ) -> dict[str, bool]:
        activation = self.get_activation(activation_id)
        if activation is None or activation.status != CoordinatorActivationStatus.ACTIVE:
            raise ConflictError("activation is not active")
        authority = activation.authority
        self.assert_activation_active(activation_id)
        if authority.deadline and _aware(authority.deadline) <= _now():
            raise AuthorityLimitError("authority deadline has expired")
        if capability not in authority.allowed_capabilities:
            raise AuthorityLimitError(f"capability is outside authority: {capability}")
        if work_id and authority.allowed_work_ids and work_id not in authority.allowed_work_ids:
            raise AuthorityLimitError(f"work item is outside authority: {work_id}")
        if (
            repository_id
            and authority.allowed_repository_ids
            and repository_id not in authority.allowed_repository_ids
        ):
            raise AuthorityLimitError(f"repository is outside authority: {repository_id}")
        if path and ".." in path.replace("\\", "/").split("/"):
            raise AuthorityLimitError("path traversal is outside authority")
        if (
            path
            and authority.allowed_paths
            and not any(_within(path, root) for root in authority.allowed_paths)
        ):
            raise AuthorityLimitError(f"path is outside authority: {path}")
        if expand_scope and not authority.may_expand_scope:
            raise AuthorityLimitError("scope expansion requires approval")
        return {"allowed": True}

    def commit_checkpoint(self, activation_id: str, checkpoint: RoleCheckpoint) -> RoleCheckpoint:
        with self.database.session() as session:
            activation = self._require_active_activation(session, activation_id)
            role = session.get(CoordinatorRoleRow, activation.role_id)
            assert role is not None
            self._validate_activation_lease(session, activation)
            if checkpoint.role_id != activation.role_id:
                raise ValueError("checkpoint role does not match activation")
            if checkpoint.fencing_token != activation.fencing_token:
                raise StaleFencingTokenError("checkpoint fencing token does not match activation")
            if checkpoint.version != role.checkpoint_version + 1:
                raise ConflictError("checkpoint version is stale")
            if checkpoint.charter != role.charter:
                raise ValueError("checkpoint charter does not match durable role charter")
            if checkpoint.authority_profile != role.authority_profile:
                raise ValueError("checkpoint authority profile does not match durable role")
            if not checkpoint.parent_summary.strip():
                raise ValueError("checkpoint parent summary cannot be blank")
            overlap = set(checkpoint.active_delegations) & set(checkpoint.completed_delegations)
            if overlap:
                raise ValueError("delegations cannot be both active and completed")
        persisted = self.roles.append_checkpoint(checkpoint)
        with self.database.session() as session:
            activation = self._require_active_activation(session, activation_id)
            activation.checkpoint_version_after = checkpoint.version
            activation.updated_at = _now()
            session.commit()
        return persisted

    def complete_activation(
        self,
        activation_id: str,
        *,
        intake_status: CoordinatorIntakeStatus = CoordinatorIntakeStatus.COMPLETED,
        intake_executed: bool = True,
        attention_required: str | None = None,
    ) -> CoordinatorActivation:
        with self.database.session() as session:
            row = self._require_active_activation(session, activation_id)
            self._validate_activation_lease(session, row)
            if row.checkpoint_version_after is None:
                raise ConflictError("activation cannot complete without a checkpoint")
            role_id, holder, token = row.role_id, row.holder_id, row.fencing_token
            row.status = str(CoordinatorActivationStatus.COMPLETED)
            row.completed_at = row.updated_at = _now()
            intake_id = row.intake_request_id
            session.commit()
        self.roles.release_role_lease(role_id, holder, token)
        if intake_id:
            self.update_intake(
                intake_id,
                status=intake_status,
                executed=intake_executed,
                approval_required=intake_status == CoordinatorIntakeStatus.AWAITING_APPROVAL,
                attention_required=attention_required,
            )
        result = self.get_activation(activation_id)
        assert result is not None
        return result

    def fail_activation(self, activation_id: str, error: str) -> CoordinatorActivation:
        with self.database.session() as session:
            row = self._require_active_activation(session, activation_id)
            role_id, holder, token = row.role_id, row.holder_id, row.fencing_token
            row.status = str(CoordinatorActivationStatus.FAILED)
            row.error = error
            row.completed_at = row.updated_at = _now()
            intake_id = row.intake_request_id
            session.commit()
        self.roles.release_role_lease(role_id, holder, token)
        if intake_id:
            self.update_intake(
                intake_id,
                status=CoordinatorIntakeStatus.FAILED,
                attention_required=error,
            )
        result = self.get_activation(activation_id)
        assert result is not None
        return result

    def release_activation(self, activation_id: str) -> None:
        activation = self.get_activation(activation_id)
        if activation is None:
            raise NotFoundError(f"unknown activation: {activation_id}")
        if activation.status != CoordinatorActivationStatus.ACTIVE:
            return
        self.roles.release_role_lease(
            activation.role_id, activation.holder_id, activation.fencing_token
        )
        with self.database.session() as session:
            row = session.get(CoordinatorActivationRow, activation_id)
            assert row is not None
            row.status = str(CoordinatorActivationStatus.EXPIRED)
            row.completed_at = row.updated_at = _now()
            session.commit()

    def expire_stale_activations(self) -> int:
        now = _now()
        expired = 0
        with self.database.session() as session:
            rows = session.scalars(
                select(CoordinatorActivationRow).where(
                    CoordinatorActivationRow.status == CoordinatorActivationStatus.ACTIVE
                )
            ).all()
            for row in rows:
                lease = session.get(RoleLeaseRow, row.role_id)
                if (
                    lease is None
                    or lease.fencing_token != row.fencing_token
                    or lease.holder_id != row.holder_id
                    or _aware(lease.expires_at) <= now
                ):
                    row.status = str(CoordinatorActivationStatus.EXPIRED)
                    row.completed_at = row.updated_at = now
                    expired += 1
            session.commit()
        return expired

    # Context, reporting, rollups -------------------------------------
    def assemble_context(
        self, role_id: str, *, intake_request_id: str | None = None
    ) -> dict[str, Any]:
        role = self.roles.get_role(role_id)
        if role is None:
            raise NotFoundError(f"unknown role: {role_id}")
        checkpoint = self.roles.get_latest_checkpoint(role_id)
        reports = self.roles.list_reports(recipient_role_id=role_id)
        work_id = role.scope.removeprefix("work:") if role.scope.startswith("work:") else None
        work = self.roles.get_work(work_id) if work_id else None
        conversation = None
        if role.current_conversation_id:
            with self.database.session() as session:
                row = session.get(ConversationRow, role.current_conversation_id)
                conversation = row.as_dict() if row else None
        intake = self.get_intake(intake_request_id) if intake_request_id else None
        rollups = self.list_rollups(role_id)
        unresolved = {
            "open_questions": checkpoint.open_questions if checkpoint else [],
            "blockers": checkpoint.blockers if checkpoint else [],
            "dependencies": checkpoint.dependencies if checkpoint else [],
            "active_delegations": checkpoint.active_delegations if checkpoint else [],
            "recommended_next_action": checkpoint.recommended_next_action if checkpoint else None,
        }
        return {
            "role": role.model_dump(mode="json"),
            "work_item": work.model_dump(mode="json") if work else None,
            "intake": intake.model_dump(mode="json") if intake else None,
            "checkpoint": checkpoint.model_dump(mode="json") if checkpoint else None,
            "unresolved": unresolved,
            "child_reports": [report.model_dump(mode="json") for report in reports[-50:]],
            "rollups": [rollup.model_dump(mode="json") for rollup in rollups],
            "conversation_history": self.roles.list_role_conversations(role_id),
            "current_provider_locator": conversation,
        }

    def bootstrap_portfolio_role(self) -> CoordinatorRole:
        role_id = "role-portfolio-coordinator"
        existing = self.roles.get_role(role_id)
        if existing is not None:
            return existing
        role = CoordinatorRole(
            role_id=role_id,
            role_type="portfolio_coordinator",
            scope="portfolio",
            charter=(
                "Interpret user objectives, route them to durable work, expose proposed topology, "
                "and surface decisions that require user attention."
            ),
            authority_profile="delegate-bounded",
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            status=RoleStatus.ACTIVE,
        )
        try:
            return self.roles.create_role(role)
        except ConflictError:
            existing = self.roles.get_role(role_id)
            assert existing is not None
            return existing

    def submit_child_report(self, report: RoleReport) -> RoleReport:
        child = self.roles.get_role(report.reporting_role_id)
        if child is None:
            raise NotFoundError(f"unknown role: {report.reporting_role_id}")
        if child.parent_role_id != report.recipient_role_id:
            raise ConflictError("report recipient is not the child's primary reporting parent")
        persisted = self.roles.append_report(report)
        with self.database.session() as session:
            key = _rollup_key(report.recipient_role_id, report.reporting_role_id)
            if session.get(RoleRollupStateRow, key) is None:
                session.add(
                    RoleRollupStateRow(
                        rollup_id=key,
                        parent_role_id=report.recipient_role_id,
                        child_role_id=report.reporting_role_id,
                        incorporated_checkpoint_version=0,
                        updated_at=_now(),
                    )
                )
                session.commit()
        return persisted

    def record_rollup(
        self,
        activation_id: str,
        *,
        child_role_id: str,
        checkpoint_version: int,
        report_id: str | None = None,
    ) -> RoleRollupState:
        with self.database.session() as session:
            activation = self._require_active_activation(session, activation_id)
            self._validate_activation_lease(session, activation)
            parent = session.get(CoordinatorRoleRow, activation.role_id)
            child = session.get(CoordinatorRoleRow, child_role_id)
            if parent is None or child is None:
                raise NotFoundError("parent or child role not found")
            if child.parent_role_id != parent.role_id:
                raise ConflictError("role is not a direct reporting child")
            if checkpoint_version > child.checkpoint_version:
                raise ConflictError("rollup references an unpublished child checkpoint")
            if report_id:
                report = session.get(RoleReportRow, report_id)
                if (
                    report is None
                    or report.reporting_role_id != child_role_id
                    or report.recipient_role_id != parent.role_id
                    or report.checkpoint_version != checkpoint_version
                ):
                    raise ValueError("report does not match the incorporated child checkpoint")
            key = _rollup_key(parent.role_id, child_role_id)
            row = session.get(RoleRollupStateRow, key)
            if row is None:
                row = RoleRollupStateRow(
                    rollup_id=key,
                    parent_role_id=parent.role_id,
                    child_role_id=child_role_id,
                    incorporated_checkpoint_version=checkpoint_version,
                    updated_at=_now(),
                )
                session.add(row)
            elif checkpoint_version < row.incorporated_checkpoint_version:
                raise ConflictError("rollup checkpoint cannot move backwards")
            row.incorporated_checkpoint_version = checkpoint_version
            row.report_id = report_id
            row.updated_at = _now()
            session.commit()
            return _rollup_model(row, child.checkpoint_version)

    def list_rollups(self, parent_role_id: str) -> list[RoleRollupState]:
        with self.database.session() as session:
            if session.get(CoordinatorRoleRow, parent_role_id) is None:
                raise NotFoundError(f"unknown role: {parent_role_id}")
            children = session.scalars(
                select(CoordinatorRoleRow)
                .where(CoordinatorRoleRow.parent_role_id == parent_role_id)
                .order_by(CoordinatorRoleRow.role_id)
            ).all()
            states = {
                row.child_role_id: row
                for row in session.scalars(
                    select(RoleRollupStateRow).where(
                        RoleRollupStateRow.parent_role_id == parent_role_id
                    )
                )
            }
            result = []
            for child in children:
                row = states.get(child.role_id)
                if row is None:
                    result.append(
                        RoleRollupState(
                            parent_role_id=parent_role_id,
                            child_role_id=child.role_id,
                            incorporated_checkpoint_version=0,
                            current_checkpoint_version=child.checkpoint_version,
                            stale=child.checkpoint_version > 0,
                        )
                    )
                else:
                    result.append(_rollup_model(row, child.checkpoint_version))
            return result

    # Internal validation ---------------------------------------------
    @staticmethod
    def _validate_authority(
        mode: AutonomyMode,
        authority: AuthorityLimits,
        request: WorkRequest | None,
    ) -> None:
        if mode != AutonomyMode.AUTONOMOUS:
            return
        if not authority.allowed_capabilities:
            raise ValueError("autonomous mode requires explicit allowed capabilities")
        if not authority.allowed_work_ids and not (request and request.work_id):
            raise ValueError("autonomous mode requires an explicit work scope")
        if authority.token_budget is None and authority.cost_budget_usd is None:
            raise ValueError("autonomous mode requires a finite token or cost budget")
        if authority.deadline is None:
            raise ValueError("autonomous mode requires a finite deadline")
        if _aware(authority.deadline) <= _now():
            raise ValueError("autonomous authority deadline must be in the future")

    @staticmethod
    def _enforce_usage(authority: AuthorityLimits, usage: AuthorityUsage) -> None:
        if usage.active_executions > authority.max_parallel_executions:
            raise AuthorityLimitError("parallel execution authority exhausted")
        if usage.attempts_used > authority.max_attempts:
            raise AuthorityLimitError("attempt authority exhausted")
        if authority.token_budget is not None and usage.tokens_used > authority.token_budget:
            raise AuthorityLimitError("token budget exhausted")
        if (
            authority.cost_budget_usd is not None
            and usage.cost_used_usd > authority.cost_budget_usd
        ):
            raise AuthorityLimitError("cost budget exhausted")
        if authority.deadline and _aware(authority.deadline) <= _now():
            raise AuthorityLimitError("authority deadline has expired")

    @staticmethod
    def _validate_not_broader(candidate: AuthorityLimits, granted: AuthorityLimits) -> None:
        if candidate.max_parallel_executions > granted.max_parallel_executions:
            raise AuthorityLimitError("parallel authority override exceeds intake grant")
        if candidate.max_attempts > granted.max_attempts:
            raise AuthorityLimitError("attempt authority override exceeds intake grant")
        if granted.token_budget is not None and (
            candidate.token_budget is None or candidate.token_budget > granted.token_budget
        ):
            raise AuthorityLimitError("token authority override exceeds intake grant")
        if granted.cost_budget_usd is not None and (
            candidate.cost_budget_usd is None or candidate.cost_budget_usd > granted.cost_budget_usd
        ):
            raise AuthorityLimitError("cost authority override exceeds intake grant")
        if not set(candidate.allowed_capabilities).issubset(granted.allowed_capabilities):
            raise AuthorityLimitError("capability authority override exceeds intake grant")
        if not set(candidate.allowed_work_ids).issubset(granted.allowed_work_ids):
            raise AuthorityLimitError("work authority override exceeds intake grant")
        if not set(candidate.allowed_repository_ids).issubset(granted.allowed_repository_ids):
            raise AuthorityLimitError("repository authority override exceeds intake grant")
        if not set(candidate.allowed_paths).issubset(granted.allowed_paths):
            raise AuthorityLimitError("path authority override exceeds intake grant")
        if candidate.may_expand_scope and not granted.may_expand_scope:
            raise AuthorityLimitError("scope expansion override exceeds intake grant")
        if granted.deadline and (
            candidate.deadline is None or _aware(candidate.deadline) > _aware(granted.deadline)
        ):
            raise AuthorityLimitError("deadline override exceeds intake grant")

    @staticmethod
    def _validate_approved_actions(
        actions: list[dict[str, Any]], authority: AuthorityLimits
    ) -> None:
        if len(actions) > authority.max_parallel_executions:
            raise ValueError("approved actions exceed max_parallel_executions")
        attempts = sum(int(item.get("attempt_count", 1)) for item in actions)
        if attempts > authority.max_attempts:
            raise ValueError("approved actions exceed max_attempts")
        for action in actions:
            capability = action.get("capability")
            if not isinstance(capability, str) or capability not in authority.allowed_capabilities:
                raise ValueError(f"approved action capability is outside authority: {capability}")
            if not action.get("target_id") or not action.get("scope"):
                raise ValueError("approved execution actions require explicit target and scope")
        estimated_tokens = sum(int(item.get("estimated_tokens", 0)) for item in actions)
        if authority.token_budget is not None and estimated_tokens > authority.token_budget:
            raise ValueError("approved actions exceed token budget")
        estimated_cost = sum(float(item.get("estimated_cost_usd", 0)) for item in actions)
        if authority.cost_budget_usd is not None and estimated_cost > authority.cost_budget_usd:
            raise ValueError("approved actions exceed cost budget")

    @staticmethod
    def _require_active_activation(
        session: Session, activation_id: str
    ) -> CoordinatorActivationRow:
        row = session.get(CoordinatorActivationRow, activation_id)
        if row is None:
            raise NotFoundError(f"unknown activation: {activation_id}")
        if row.status != CoordinatorActivationStatus.ACTIVE:
            raise ConflictError(f"activation is not active: {row.status}")
        return row

    @staticmethod
    def _validate_activation_lease(session: Session, activation: CoordinatorActivationRow) -> None:
        lease = session.get(RoleLeaseRow, activation.role_id)
        if (
            lease is None
            or lease.holder_id != activation.holder_id
            or lease.fencing_token != activation.fencing_token
            or _aware(lease.expires_at) <= _now()
        ):
            raise StaleFencingTokenError("activation lease or fencing token is stale")

    @staticmethod
    def _append_intake_event(
        session: Session, request_id: str, event_type: str, data: dict[str, Any]
    ) -> None:
        previous = session.scalar(
            select(func.max(CoordinatorIntakeEventRow.sequence)).where(
                CoordinatorIntakeEventRow.request_id == request_id
            )
        )
        session.add(
            CoordinatorIntakeEventRow(
                event_id=f"intake-event-{uuid4().hex}",
                request_id=request_id,
                sequence=0 if previous is None else int(previous) + 1,
                type=event_type,
                data_json=_json(data),
                occurred_at=_now(),
            )
        )


def _intake_row(intake: CoordinatorIntake) -> CoordinatorIntakeRow:
    return CoordinatorIntakeRow(
        request_id=intake.request_id,
        request_json=intake.request.model_dump_json(),
        mode=str(intake.request.mode),
        status=str(intake.status),
        routed_work_id=intake.routed_work_id,
        routed_role_id=intake.routed_role_id,
        proposed_actions_json=_json(intake.proposed_actions),
        proposed_topology_json=_json(intake.proposed_topology),
        attention_required=intake.attention_required,
        approval_required=intake.approval_required,
        executed=intake.executed,
        decision_note=intake.decision_note,
        created_at=intake.created_at,
        updated_at=intake.updated_at,
    )


def _intake_model(row: CoordinatorIntakeRow) -> CoordinatorIntake:
    return CoordinatorIntake(
        request_id=row.request_id,
        request=WorkRequest.model_validate_json(row.request_json),
        status=CoordinatorIntakeStatus(row.status),
        routed_work_id=row.routed_work_id,
        routed_role_id=row.routed_role_id,
        proposed_actions=json.loads(row.proposed_actions_json),
        proposed_topology=json.loads(row.proposed_topology_json),
        attention_required=row.attention_required,
        approval_required=row.approval_required,
        executed=row.executed,
        decision_note=row.decision_note,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _activation_model(row: CoordinatorActivationRow) -> CoordinatorActivation:
    return CoordinatorActivation(
        activation_id=row.activation_id,
        role_id=row.role_id,
        intake_request_id=row.intake_request_id,
        holder_id=row.holder_id,
        fencing_token=row.fencing_token,
        status=CoordinatorActivationStatus(row.status),
        checkpoint_version_before=row.checkpoint_version_before,
        checkpoint_version_after=row.checkpoint_version_after,
        conversation_id=row.conversation_id,
        authority=AuthorityLimits.model_validate_json(row.authority_json),
        usage=AuthorityUsage.model_validate_json(row.usage_json),
        started_at=_aware(row.started_at),
        updated_at=_aware(row.updated_at),
        completed_at=_optional_aware(row.completed_at),
        error=row.error,
    )


def _rollup_model(row: RoleRollupStateRow, current: int) -> RoleRollupState:
    return RoleRollupState(
        parent_role_id=row.parent_role_id,
        child_role_id=row.child_role_id,
        incorporated_checkpoint_version=row.incorporated_checkpoint_version,
        current_checkpoint_version=current,
        report_id=row.report_id,
        stale=current > row.incorporated_checkpoint_version,
        updated_at=_aware(row.updated_at),
    )


def _rollup_key(parent: str, child: str) -> str:
    return f"{parent}:{child}"


def _within(path: str, root: str) -> bool:
    normalized_path = path.replace("\\", "/").rstrip("/")
    normalized_root = root.replace("\\", "/").rstrip("/")
    return normalized_path == normalized_root or normalized_path.startswith(f"{normalized_root}/")


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _optional_aware(value: datetime | None) -> datetime | None:
    return _aware(value) if value else None


def _iso(value: datetime) -> str:
    return _aware(value).isoformat()
