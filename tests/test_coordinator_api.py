from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from agent_bridge_catalog.app import create_app
from agent_bridge_catalog.config import Settings
from agent_bridge_protocol.models import CoordinatorIntakeStatus


def settings(tmp_path: Path) -> Settings:
    return Settings(
        state_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'coordinator-api.db'}",
        node_id="hub",
        environment_id="test",
    )


class AvailableRuntime:
    def __init__(self) -> None:
        self.scheduled: list[tuple[str, str | None]] = []

    def require_available(self) -> None:
        return None

    def schedule(self, request_id: str, *, role_id: str | None = None) -> None:
        self.scheduled.append((request_id, role_id))

    def status(self) -> dict[str, object]:
        return {"status": "available", "active_intake_ids": []}


def test_intake_manual_bypass_list_and_decision(tmp_path: Path) -> None:
    app = create_app(settings=settings(tmp_path))
    with TestClient(app) as client:
        app.state.coordinator_runtime = AvailableRuntime()
        app.state.coordinator_store.bootstrap_portfolio_role()
        manual = client.post(
            "/api/v1/coordinator/intake",
            json={"objective": "Run directly", "mode": "manual"},
        )
        assert manual.status_code == 409
        assert "/api/v1/bridge/requests" in manual.json()["detail"]

        submitted = client.post(
            "/api/v1/coordinator/intake",
            json={
                "objective": "Plan the robot test",
                "mode": "advise",
                "target_role_id": "role-portfolio-coordinator",
            },
        )
        assert submitted.status_code == 201
        request_id = submitted.json()["request_id"]
        assert submitted.json()["request"]["requested_by"] == {
            "kind": "endpoint",
            "id": "catalog-user",
        }
        listing = client.get("/api/v1/coordinator/intake", params={"mode": "advise"}).json()
        assert listing["total"] == 1
        assert listing["items"][0]["request_id"] == request_id

        app.state.coordinator_store.update_intake(
            request_id,
            status=CoordinatorIntakeStatus.AWAITING_APPROVAL,
            approval_required=True,
            proposed_actions=[{"action_id": "recommend-1"}],
        )
        decision = client.post(
            f"/api/v1/coordinator/intake/{request_id}/decision",
            json={"decision": "approve", "note": "Looks good"},
        )
        assert decision.status_code == 200
        assert decision.json()["status"] == "approved"
        assert decision.json()["decision_note"] == "Looks good"
        events = client.get(f"/api/v1/coordinator/intake/{request_id}/events").json()
        assert events["items"][-1]["type"] == "intake.approved"


def test_autonomous_api_requires_explicit_bounds(tmp_path: Path) -> None:
    app = create_app(settings=settings(tmp_path))
    with TestClient(app) as client:
        app.state.coordinator_runtime = AvailableRuntime()
        app.state.coordinator_store.bootstrap_portfolio_role()
        missing = client.post(
            "/api/v1/coordinator/intake",
            json={
                "objective": "Autonomously test",
                "mode": "autonomous",
                "work_id": "robot",
                "target_role_id": "role-portfolio-coordinator",
                "authority": {
                    "token_budget": 1000,
                    "deadline": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "allowed_capabilities": ["robot-test"],
                },
            },
        )
        assert missing.status_code == 422
        assert "must be explicit" in missing.text

        bounded = client.post(
            "/api/v1/coordinator/intake",
            json={
                "objective": "Autonomously test",
                "mode": "autonomous",
                "work_id": "robot",
                "target_role_id": "role-portfolio-coordinator",
                "authority": {
                    "max_parallel_executions": 1,
                    "max_attempts": 2,
                    "token_budget": 1000,
                    "deadline": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "allowed_capabilities": ["robot-test"],
                    "allowed_work_ids": ["robot"],
                    "may_expand_scope": False,
                },
            },
        )
        assert bounded.status_code == 201
        assert bounded.json()["request"]["authority"]["token_budget"] == 1000


def test_advise_approval_transitions_to_scheduled_bounded_delegate(tmp_path: Path) -> None:
    app = create_app(settings=settings(tmp_path))
    with TestClient(app) as client:
        runtime = AvailableRuntime()
        app.state.coordinator_runtime = runtime
        app.state.coordinator_store.bootstrap_portfolio_role()
        created = client.post(
            "/api/v1/coordinator/intake",
            json={
                "objective": "Recommend a robot test",
                "mode": "advise",
                "target_role_id": "role-portfolio-coordinator",
            },
        ).json()
        request_id = created["request_id"]
        app.state.coordinator_store.update_intake(
            request_id,
            status=CoordinatorIntakeStatus.AWAITING_APPROVAL,
            approval_required=True,
            proposed_actions=[
                {
                    "action_id": "run-approved",
                    "type": "execute",
                    "summary": "Run approved test",
                    "target_id": "robot-node",
                    "capability": "robot-test",
                    "scope": "portfolio",
                    "attempt_count": 1,
                    "estimated_tokens": 20,
                    "estimated_cost_usd": 0,
                    "expands_scope": False,
                    "payload": {},
                }
            ],
        )
        approved = client.post(
            f"/api/v1/coordinator/intake/{request_id}/decision",
            json={
                "decision": "approve",
                "authority": {
                    "max_parallel_executions": 1,
                    "max_attempts": 2,
                    "token_budget": 100,
                    "allowed_capabilities": ["robot-test"],
                },
            },
        )
        assert approved.status_code == 200
        assert approved.json()["request"]["mode"] == "delegate"
        assert approved.json()["request"]["context"]["approved_action_ids"] == ["run-approved"]
        assert runtime.scheduled[-1] == (request_id, "role-portfolio-coordinator")


def test_role_context_and_rollup_endpoints(tmp_path: Path) -> None:
    app = create_app(settings=settings(tmp_path))
    with TestClient(app) as client:
        app.state.coordinator_runtime = AvailableRuntime()
        app.state.coordinator_store.bootstrap_portfolio_role()
        context = client.get("/api/v1/coordinator/roles/role-portfolio-coordinator/context")
        assert context.status_code == 200
        assert context.json()["role"]["role_type"] == "portfolio_coordinator"
        rollups = client.get("/api/v1/coordinator/roles/role-portfolio-coordinator/rollups")
        assert rollups.status_code == 200
        assert rollups.json() == {"items": [], "total": 0}
