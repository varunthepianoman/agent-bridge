from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent_bridge_bridge.logging_context import (
    bind_log_context,
    current_log_context,
    structured_extra,
)
from agent_bridge_catalog.app import create_app
from agent_bridge_catalog.config import Settings
from agent_bridge_catalog.supervision import BackgroundSupervisor
from agent_bridge_protocol.models import (
    ArtifactRef,
    BridgeEnvelope,
    CoordinatorRole,
    ExecutionAttempt,
    ExecutionResult,
    ExecutionStatus,
    RoleCheckpoint,
    RoleStatus,
)


@dataclass
class _Ack:
    stream: str = "BRIDGE_WORK_V1"
    sequence: int = 1
    duplicate: bool = False


class _Publisher:
    async def publish(self, envelope: BridgeEnvelope, *, subject: str | None = None) -> _Ack:
        del envelope, subject
        return _Ack()


def _settings(tmp_path: Path, **changes: Any) -> Settings:
    base = Settings(
        state_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'observability.db'}",
        node_id="hub",
        environment_id="test",
    )
    return replace(base, **changes)


def test_operational_overview_lists_pending_artifacts_roles_nodes_and_diagnostics(
    tmp_path: Path,
) -> None:
    app = create_app(settings=_settings(tmp_path), bridge_publisher=_Publisher())
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/bridge/requests",
            json={
                "request": {
                    "operation": "new_execution",
                    "instruction": "Run observed work",
                    "target": {"kind": "node", "id": "node-offline"},
                    "work_id": "work-observed",
                    "artifacts": [
                        {
                            "artifact_id": "artifact-log",
                            "name": "run.log",
                            "uri": "file:///tmp/run.log",
                            "media_type": "text/plain",
                        }
                    ],
                }
            },
        )
        assert created.status_code == 201
        execution_id = created.json()["execution"]["execution_id"]
        attempt_id = created.json()["execution"]["attempts"][0]["attempt_id"]
        provisioned = client.post(
            "/api/v1/nodes",
            json={
                "node_id": "node-offline",
                "display_name": "Offline node",
                "platform": "linux",
            },
        )
        assert provisioned.status_code == 201

        pending = client.get("/api/v1/observability/pending-requests").json()
        assert pending["total"] == 1
        assert "instruction" not in pending["items"][0]
        artifacts = client.get("/api/v1/observability/artifacts").json()
        assert artifacts["items"][0]["artifact_id"] == "artifact-log"
        assert client.get("/api/v1/observability/executions").json()["total"] == 1
        assert client.get("/api/v1/observability/roles").json()["total"] == 1
        assert client.get("/api/v1/observability/nodes").json()["total"] == 1

        summary = client.get("/api/v1/observability/summary").json()
        assert summary["status"] == "degraded"
        assert summary["broker_status"] == "unconfigured"
        assert summary["counts"]["pending_requests"] == 1
        advisories = client.get("/api/v1/observability/advisories").json()["items"]
        assert any(item["code"] == "node_unreachable" for item in advisories)
        assert client.get("/api/v1/diagnostics/background").json()["status"] == "healthy"
        health = client.get("/api/v1/health").json()
        assert health["broker_required"] is False
        assert health["broker_configured"] is False
        assert health["broker_connected"] is None
        assert client.get("/api/v1/diagnostics/messages").status_code == 200
        assert client.get("/metrics").status_code == 404

        role_store = app.state.role_store
        child = role_store.create_role(
            CoordinatorRole(
                role_id="role-observed-child",
                role_type="work_coordinator",
                scope="work:work-observed",
                parent_role_id="role-portfolio-coordinator",
                charter="Observe durable work",
                authority_profile="delegate-bounded",
            )
        )
        role_lease = role_store.acquire_role_lease(child.role_id, "test-holder", 60)
        role_store.append_checkpoint(
            RoleCheckpoint(
                role_id=child.role_id,
                version=1,
                fencing_token=role_lease.fencing_token,
                objective="Observe",
                charter=child.charter,
                authority_profile=child.authority_profile,
                status=RoleStatus.BLOCKED,
                blockers=["waiting for robot"],
                evidence=[
                    ArtifactRef(
                        artifact_id="artifact-checkpoint",
                        name="checkpoint.txt",
                        uri="file:///tmp/checkpoint.txt",
                    )
                ],
                recommended_next_action="Wake the robot node",
                parent_summary="Robot validation is blocked",
            )
        )
        roles = client.get("/api/v1/observability/roles").json()["items"]
        observed_role = next(item for item in roles if item["role_id"] == child.role_id)
        assert observed_role["latest_checkpoint"]["summary"] == ("Robot validation is blocked")
        assert observed_role["latest_checkpoint"]["blockers"] == ["waiting for robot"]
        assert observed_role["rollup"]["stale"] is True

        service = app.state.manual_bridge_service
        service.record_attempt(
            ExecutionAttempt(
                attempt_id=attempt_id,
                execution_id=execution_id,
                attempt_number=1,
                node_id="node-offline",
                status=ExecutionStatus.RUNNING,
                started_at=datetime.now(UTC),
            )
        )
        app.state.broker_projection_store.materialize_message(
            message_id="lease-message",
            subject="bridge.v1.inbox.node.node-offline",
            message_type="request",
            state="delivered",
        )
        app.state.broker_projection_store.materialize_delivery(
            delivery_id="delivery-lease",
            message_id="lease-message",
            stream="BRIDGE_WORK_V1",
            consumer="node-offline",
            delivery_sequence=1,
            state="leased",
            delivered_at=datetime.now(UTC),
            ack_deadline_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        leases = client.get("/api/v1/observability/leases").json()["items"]
        assert {(item["lease_type"], item["source"]) for item in leases}.issuperset(
            {
                ("role", "catalog.role_leases"),
                ("execution_state", "catalog.bridge_executions"),
                ("broker_delivery", "broker_projection.deliveries"),
            }
        )

        service.ingest_result(
            ExecutionResult(
                execution_id=execution_id,
                attempt_id=attempt_id,
                summary="Observed result",
                artifacts=[
                    ArtifactRef(
                        artifact_id="artifact-result",
                        name="result.json",
                        uri="file:///tmp/result.json",
                    )
                ],
            )
        )
        all_artifacts = client.get("/api/v1/observability/artifacts").json()["items"]
        by_id = {item["artifact_id"]: item for item in all_artifacts}
        assert by_id["artifact-result"]["sources"][0]["source_type"] == ("execution_result")
        assert by_id["artifact-checkpoint"]["sources"][0]["source_type"] == ("role_checkpoint")


def test_required_disconnected_broker_fails_health_check(tmp_path: Path) -> None:
    app = create_app(
        settings=_settings(
            tmp_path,
            nats_servers=("nats://broker:4222",),
            broker_required=True,
        ),
        bridge_publisher=_Publisher(),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "broker_required": True,
        "broker_configured": True,
        "broker_connected": None,
        "background": "healthy",
    }


def test_prometheus_and_injected_telemetry_export_are_optional(tmp_path: Path) -> None:
    class Exporter:
        def __init__(self) -> None:
            self.snapshots: list[dict[str, Any]] = []

        async def export(self, snapshot: dict[str, Any]) -> None:
            self.snapshots.append(snapshot)

    exporter = Exporter()
    app = create_app(
        settings=_settings(
            tmp_path,
            metrics_enabled=True,
            telemetry_interval_seconds=0.01,
        ),
        bridge_publisher=_Publisher(),
        telemetry_exporters=(exporter,),
    )
    with TestClient(app) as client:
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "agent_bridge_pending_requests" in response.text
        for _ in range(100):
            if exporter.snapshots:
                break
            client.get("/api/v1/health")
        assert exporter.snapshots


async def test_supervisor_reports_failed_critical_background_task() -> None:
    supervisor = BackgroundSupervisor()

    async def fail() -> None:
        raise RuntimeError("projection stopped")

    supervisor.create_task(fail(), name="projection")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    snapshot = supervisor.snapshot()
    assert snapshot["status"] == "degraded"
    assert snapshot["tasks"][0]["error"] == "RuntimeError: projection stopped"
    await supervisor.stop()


def test_structured_log_context_carries_supported_identifiers() -> None:
    assert current_log_context() == {}
    with bind_log_context(
        correlation_id="corr-1",
        work_id="work-1",
        message_id="message-1",
    ):
        assert current_log_context()["work_id"] == "work-1"
        assert structured_extra(execution_id="execution-1") == {
            "agent_bridge": {
                "correlation_id": "corr-1",
                "work_id": "work-1",
                "message_id": "message-1",
                "execution_id": "execution-1",
            }
        }
    assert current_log_context() == {}
