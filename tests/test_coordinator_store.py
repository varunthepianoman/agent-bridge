from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_bridge_catalog.coordinator_store import (
    AuthorityLimitError,
    CoordinatorStore,
)
from agent_bridge_catalog.db import CoordinatorActivationRow, Database, RoleLeaseRow
from agent_bridge_catalog.roles import ConflictError, RoleStore, StaleFencingTokenError
from agent_bridge_protocol.models import (
    AuthorityLimits,
    AutonomyMode,
    CoordinatorIntakeStatus,
    CoordinatorRole,
    EndpointKind,
    EndpointRef,
    RoleCheckpoint,
    RoleReport,
    RoleStatus,
    WorkRequest,
)


@pytest.fixture
def stores(tmp_path: Path) -> tuple[Database, RoleStore, CoordinatorStore]:
    database = Database(f"sqlite:///{tmp_path / 'coordinator.db'}")
    database.initialize()
    roles = RoleStore(database)
    coordinator = CoordinatorStore(database, roles)
    coordinator.bootstrap_portfolio_role()
    return database, roles, coordinator


def request(
    request_id: str,
    *,
    mode: AutonomyMode = AutonomyMode.DELEGATE,
    role_id: str | None = "role-portfolio-coordinator",
    authority: AuthorityLimits | None = None,
) -> WorkRequest:
    return WorkRequest(
        request_id=request_id,
        objective="Coordinate a durable robot test",
        mode=mode,
        requested_by=EndpointRef(kind=EndpointKind.ENDPOINT, id="user"),
        target_role_id=role_id,
        authority=authority or AuthorityLimits(),
    )


def checkpoint(role_id: str, token: int, version: int, *, charter: str) -> RoleCheckpoint:
    return RoleCheckpoint(
        role_id=role_id,
        version=version,
        fencing_token=token,
        objective="Coordinate test",
        charter=charter,
        authority_profile="delegate-bounded",
        status=RoleStatus.ACTIVE,
        current_plan=["Run test"],
        parent_summary="Test coordination is active.",
    )


def test_manual_bypasses_intake_and_autonomous_requires_bounded_authority(
    stores: tuple[Database, RoleStore, CoordinatorStore],
) -> None:
    _, _, coordinator = stores
    with pytest.raises(ConflictError, match="manual mode bypasses"):
        coordinator.create_intake(request("manual", mode=AutonomyMode.MANUAL))
    with pytest.raises(ValueError, match="allowed capabilities"):
        coordinator.create_intake(request("auto", mode=AutonomyMode.AUTONOMOUS))

    bounded = AuthorityLimits(
        max_parallel_executions=2,
        max_attempts=4,
        token_budget=10_000,
        deadline=datetime.now(UTC) + timedelta(hours=1),
        allowed_capabilities=["robot-test"],
        allowed_work_ids=["work-robot"],
    )
    created = coordinator.create_intake(
        WorkRequest(
            **request("auto-ok", mode=AutonomyMode.AUTONOMOUS, authority=bounded).model_dump(
                exclude={"work_id"}
            ),
            work_id="work-robot",
        )
    )
    assert created.status == CoordinatorIntakeStatus.SUBMITTED
    assert coordinator.list_intake_events("auto-ok")[0]["type"] == "intake.submitted"


def test_advise_plans_before_approval_and_approved_intake_can_activate(
    stores: tuple[Database, RoleStore, CoordinatorStore],
) -> None:
    _, _, coordinator = stores
    intake = coordinator.create_intake(request("advise", mode=AutonomyMode.ADVISE))
    assert intake.approval_required is False
    activation = coordinator.begin_activation(
        "role-portfolio-coordinator",
        holder_id="engine-a",
        intake_request_id="advise",
    )
    activation_id = activation["activation"]["activation_id"]
    coordinator.fail_activation(activation_id, "planned recommendations need approval")

    second = coordinator.create_intake(request("decision", mode=AutonomyMode.ADVISE))
    coordinator.update_intake(
        second.request_id,
        status=CoordinatorIntakeStatus.AWAITING_APPROVAL,
        approval_required=True,
        proposed_actions=[{"id": "action-1"}],
    )
    approved = coordinator.decide_intake(second.request_id, approved=True, note="Proceed")
    assert approved.status == CoordinatorIntakeStatus.APPROVED
    assert approved.decision_note == "Proceed"
    result = coordinator.begin_activation(
        "role-portfolio-coordinator",
        holder_id="engine-b",
        intake_request_id=second.request_id,
    )
    assert result["activation"]["status"] == "active"


def test_advise_execution_approval_promotes_to_bounded_delegate(
    stores: tuple[Database, RoleStore, CoordinatorStore],
) -> None:
    _, _, coordinator = stores
    intake = coordinator.create_intake(request("approve-exec", mode=AutonomyMode.ADVISE))
    action = {
        "action_id": "run-robot",
        "type": "execute",
        "summary": "Run robot tests",
        "target_id": "robot-node",
        "capability": "robot-test",
        "scope": "portfolio",
        "expands_scope": False,
        "attempt_count": 1,
        "estimated_tokens": 100,
        "estimated_cost_usd": 0.1,
        "payload": {},
    }
    coordinator.update_intake(
        intake.request_id,
        status=CoordinatorIntakeStatus.AWAITING_APPROVAL,
        approval_required=True,
        proposed_actions=[action],
    )
    with pytest.raises(ValueError, match="explicit bounded authority"):
        coordinator.decide_intake(intake.request_id, approved=True)

    authority = AuthorityLimits(
        max_parallel_executions=1,
        max_attempts=2,
        token_budget=1_000,
        cost_budget_usd=1,
        allowed_capabilities=["robot-test"],
    )
    approved = coordinator.decide_intake(intake.request_id, approved=True, authority=authority)
    assert approved.request.mode == AutonomyMode.DELEGATE
    assert approved.request.context["approved_action_ids"] == ["run-robot"]
    assert approved.request.context["approved_actions"] == [action]


def test_activation_scope_authority_duplicate_usage_and_fencing(
    stores: tuple[Database, RoleStore, CoordinatorStore],
) -> None:
    database, roles, coordinator = stores
    child = roles.create_role(
        CoordinatorRole(
            role_id="role-work-a",
            role_type="work_coordinator",
            scope="work:a",
            charter="Coordinate work A",
            authority_profile="delegate-bounded",
            parent_role_id="role-portfolio-coordinator",
            status=RoleStatus.ACTIVE,
        )
    )
    authority = AuthorityLimits(
        max_parallel_executions=1,
        max_attempts=2,
        token_budget=100,
        allowed_capabilities=["test"],
        allowed_work_ids=["a"],
    )
    intake = coordinator.create_intake(
        WorkRequest(
            request_id="scope",
            objective="Work A",
            requested_by=EndpointRef(kind=EndpointKind.ENDPOINT, id="user"),
            work_id="a",
            target_role_id=child.role_id,
            authority=authority,
        )
    )
    started = coordinator.begin_activation(
        child.role_id,
        holder_id="holder",
        intake_request_id=intake.request_id,
    )
    activation_id = started["activation"]["activation_id"]
    with pytest.raises(ConflictError, match="active"):
        coordinator.begin_activation(
            child.role_id, holder_id="holder", intake_request_id=intake.request_id
        )
    with pytest.raises(AuthorityLimitError, match="parallel"):
        coordinator.record_usage(activation_id, active_executions_delta=2)
    coordinator.record_usage(
        activation_id,
        tokens=20,
        attempts=1,
        active_executions_delta=1,
        total_executions=1,
    )
    assert coordinator.authorize_action(activation_id, capability="test", work_id="a") == {
        "allowed": True
    }
    with pytest.raises(AuthorityLimitError, match="traversal"):
        coordinator.authorize_action(activation_id, capability="test", path="/repo/../secret")

    activation = coordinator.get_activation(activation_id)
    assert activation is not None
    with database.session() as session:
        lease = session.get(RoleLeaseRow, child.role_id)
        assert lease is not None
        lease.fencing_token += 1
        session.commit()
    with pytest.raises(StaleFencingTokenError):
        coordinator.assert_activation_active(activation_id)
    with pytest.raises(StaleFencingTokenError):
        coordinator.set_activation_conversation(activation_id, "conv-stale")


def test_checkpoint_validation_completion_and_stale_rollup(
    stores: tuple[Database, RoleStore, CoordinatorStore],
) -> None:
    _, roles, coordinator = stores
    parent = coordinator.bootstrap_portfolio_role()
    child = roles.create_role(
        CoordinatorRole(
            role_id="role-child",
            role_type="work_coordinator",
            scope="work:child",
            charter="Coordinate child work",
            authority_profile="delegate-bounded",
            parent_role_id=parent.role_id,
            status=RoleStatus.ACTIVE,
        )
    )
    child_activation = coordinator.begin_activation(child.role_id, holder_id="child-engine")
    child_activation_id = child_activation["activation"]["activation_id"]
    child_token = child_activation["activation"]["fencing_token"]
    invalid = checkpoint(child.role_id, child_token, 1, charter="Wrong charter")
    with pytest.raises(ValueError, match="charter"):
        coordinator.commit_checkpoint(child_activation_id, invalid)
    first = checkpoint(child.role_id, child_token, 1, charter=child.charter)
    coordinator.commit_checkpoint(child_activation_id, first)
    coordinator.complete_activation(child_activation_id)

    report = coordinator.submit_child_report(
        RoleReport(
            report_id="report-child-1",
            reporting_role_id=child.role_id,
            recipient_role_id=parent.role_id,
            checkpoint_version=1,
            status=RoleStatus.ACTIVE,
            summary="Child checkpoint one",
        )
    )
    assert coordinator.list_rollups(parent.role_id)[0].stale is True

    parent_activation = coordinator.begin_activation(parent.role_id, holder_id="parent-engine")
    parent_activation_id = parent_activation["activation"]["activation_id"]
    incorporated = coordinator.record_rollup(
        parent_activation_id,
        child_role_id=child.role_id,
        checkpoint_version=1,
        report_id=report.report_id,
    )
    assert incorporated.stale is False

    lease = roles.acquire_role_lease(child.role_id, "child-engine-2")
    roles.append_checkpoint(
        checkpoint(child.role_id, lease.fencing_token, 2, charter=child.charter)
    )
    assert coordinator.list_rollups(parent.role_id)[0].stale is True


def test_expired_activation_recovery(stores: tuple[Database, RoleStore, CoordinatorStore]) -> None:
    database, _, coordinator = stores
    started = coordinator.begin_activation("role-portfolio-coordinator", holder_id="engine")
    activation_id = started["activation"]["activation_id"]
    with database.session() as session:
        lease = session.get(RoleLeaseRow, "role-portfolio-coordinator")
        assert lease is not None
        lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert coordinator.expire_stale_activations() == 1
    with database.session() as session:
        row = session.get(CoordinatorActivationRow, activation_id)
        assert row is not None
        assert row.status == "expired"
