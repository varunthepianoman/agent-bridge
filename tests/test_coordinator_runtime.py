from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

from agent_bridge_catalog.app import create_app
from agent_bridge_catalog.config import Settings
from agent_bridge_catalog.coordinator_runtime import BridgeCoordinatorActionExecutor
from agent_bridge_coordinator.models import (
    BudgetUsage,
    CheckpointDraft,
    CoordinatorAction,
    CoordinatorActionType,
    CoordinatorModelOutput,
    CoordinatorSession,
    CoordinatorTurn,
)
from agent_bridge_protocol.models import BridgeEnvelope, CoordinatorRole, RoleStatus, WorkRequest


@dataclass
class _Ack:
    stream: str = "BRIDGE_WORK_V1"
    sequence: int = 1
    duplicate: bool = False


class _Publisher:
    def __init__(self) -> None:
        self.published: list[BridgeEnvelope] = []

    async def publish(self, envelope: BridgeEnvelope, *, subject: str | None = None) -> _Ack:
        del subject
        self.published.append(envelope)
        return _Ack(sequence=len(self.published))


class _Model:
    async def prepare(
        self,
        role: CoordinatorRole,
        *,
        current_conversation_id: str | None,
        current_provider_thread_id: str | None,
        cwd: str | None,
        force_new: bool = False,
    ) -> CoordinatorSession:
        del role, current_provider_thread_id, force_new
        return CoordinatorSession(
            conversation_id=current_conversation_id or "sdk-thread-1",
            provider_thread_id="sdk-thread-1",
            cwd=cwd or "/work/project",
            is_replacement=current_conversation_id is None,
        )

    async def run(self, session: CoordinatorSession, prompt: str) -> CoordinatorTurn:
        assert "Run the bounded validation" in prompt
        output = CoordinatorModelOutput(
            checkpoint=CheckpointDraft(
                objective="Run the bounded validation",
                status=RoleStatus.ACTIVE,
                current_plan=["Dispatch validation"],
                parent_summary="Validation was dispatched through the durable Bridge.",
            ),
            actions=[
                CoordinatorAction(
                    action_id="dispatch-validation",
                    type=CoordinatorActionType.EXECUTE,
                    summary="Run validation",
                    target_id="worker-node",
                    capability="test-suite",
                    scope="portfolio",
                    payload={
                        "target": {"kind": "node", "id": "worker-node"},
                    },
                )
            ],
        )
        return CoordinatorTurn(output=output, session=session, usage=BudgetUsage(attempts=1))

    async def abort(self, session: CoordinatorSession) -> None:
        del session


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        state_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'runtime.db'}",
        node_id="hub",
        environment_id="test",
    )


def test_normal_app_processes_intake_with_stable_conversation_and_bridge(tmp_path: Path) -> None:
    publisher = _Publisher()
    app = create_app(
        settings=_settings(tmp_path),
        bridge_publisher=publisher,
        coordinator_model=_Model(),
    )
    with TestClient(app) as client:
        status = client.get("/api/v1/coordinator/runtime").json()
        assert status["status"] == "available"
        created = client.post(
            "/api/v1/coordinator/intake",
            json={
                "objective": "Run the bounded validation",
                "mode": "delegate",
                "authority": {
                    "max_parallel_executions": 1,
                    "max_attempts": 3,
                    "allowed_capabilities": ["test-suite"],
                },
            },
        )
        assert created.status_code == 201
        request_id = created.json()["request_id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            intake = client.get(f"/api/v1/coordinator/intake/{request_id}").json()
            if intake["status"] in {"completed", "failed"}:
                break
            time.sleep(0.02)
        assert intake["status"] == "completed", intake
        assert intake["executed"] is True
        assert len(publisher.published) == 1
        assert publisher.published[0].destination.model_dump() == {
            "kind": "node",
            "id": "worker-node",
        }
        roles = client.get("/api/v1/roles").json()["items"]
        portfolio = next(item for item in roles if item["role_id"] == "role-portfolio-coordinator")
        assert portfolio["current_conversation_id"] != "sdk-thread-1"
        conversation = client.get(
            f"/api/v1/conversations/{portfolio['current_conversation_id']}"
        ).json()
        assert conversation["provider_thread_id"] == "sdk-thread-1"
        assert conversation["cwd"] == "/work/project"


async def test_coordinator_bridge_dispatch_is_durably_idempotent(tmp_path: Path) -> None:
    publisher = _Publisher()
    app = create_app(settings=_settings(tmp_path), bridge_publisher=publisher)
    with TestClient(app):
        executor = BridgeCoordinatorActionExecutor(app.state.manual_bridge_service)
        action = CoordinatorAction(
            action_id="same-action",
            type=CoordinatorActionType.EXECUTE,
            summary="Run once",
            target_id="worker-node",
            capability="test-suite",
            scope="portfolio",
            payload={"target": {"kind": "node", "id": "worker-node"}},
        )
        request = WorkRequest(
            request_id="same-intake",
            objective="Run once",
            mode="delegate",
            requested_by={"kind": "endpoint", "id": "user"},
            authority={"allowed_capabilities": ["test-suite"]},
        )
        role = app.state.role_store.get_role("role-portfolio-coordinator")
        assert role is not None
        await executor.execute(action, request=request, role=role)
        await executor.execute(action, request=request, role=role)
        assert len(publisher.published) == 1


def test_disabled_runtime_is_explicit_and_manual_bridge_still_works(tmp_path: Path) -> None:
    publisher = _Publisher()
    app = create_app(settings=_settings(tmp_path), bridge_publisher=publisher)
    with TestClient(app) as client:
        status = client.get("/api/v1/coordinator/runtime").json()
        assert status["status"] == "unavailable"
        intake = client.post(
            "/api/v1/coordinator/intake",
            json={"objective": "Coordinate this", "mode": "delegate"},
        )
        assert intake.status_code == 503
        manual = client.post(
            "/api/v1/bridge/messages",
            json={
                "envelope": {
                    "kind": "message",
                    "destination": {"kind": "room", "id": "manual"},
                    "body": {"instruction": "Direct request"},
                }
            },
        )
        assert manual.status_code == 201
        assert manual.json()["message"]["status"] == "published"


def test_approved_advice_transitions_to_bounded_bridge_execution(tmp_path: Path) -> None:
    publisher = _Publisher()
    app = create_app(
        settings=_settings(tmp_path),
        bridge_publisher=publisher,
        coordinator_model=_Model(),
    )
    authority = {
        "max_parallel_executions": 1,
        "max_attempts": 3,
        "allowed_capabilities": ["test-suite"],
    }
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/coordinator/intake",
            json={
                "objective": "Run the bounded validation",
                "mode": "advise",
                "authority": authority,
            },
        )
        request_id = created.json()["request_id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            intake = client.get(f"/api/v1/coordinator/intake/{request_id}").json()
            if intake["status"] == "awaiting_approval":
                break
            time.sleep(0.02)
        assert intake["status"] == "awaiting_approval", intake
        assert publisher.published == []

        approved = client.post(
            f"/api/v1/coordinator/intake/{request_id}/decision",
            json={"decision": "approve", "authority": authority},
        )
        assert approved.status_code == 200
        assert approved.json()["request"]["mode"] == "delegate"
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            intake = client.get(f"/api/v1/coordinator/intake/{request_id}").json()
            if intake["status"] in {"completed", "failed"}:
                break
            time.sleep(0.02)
        assert intake["status"] == "completed", intake
        assert intake["executed"] is True
        assert len(publisher.published) == 1
