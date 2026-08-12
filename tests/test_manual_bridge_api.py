from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import agent_bridge_catalog.app as app_module
from agent_bridge_catalog.app import create_app
from agent_bridge_catalog.config import Settings
from agent_bridge_catalog.result_projection_worker import ExecutionResultProjectionWorker
from agent_bridge_protocol import (
    BridgeEnvelope,
    EndpointKind,
    EndpointRef,
    ExecutionAttempt,
    ExecutionResult,
    ExecutionStatus,
    MessageKind,
)


@dataclass
class _Ack:
    stream: str = "BRIDGE_WORK_V1"
    sequence: int = 42
    duplicate: bool = False


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[BridgeEnvelope, str | None]] = []

    async def publish(self, envelope: BridgeEnvelope, *, subject: str | None = None) -> _Ack:
        self.published.append((envelope, subject))
        return _Ack()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        state_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'manual.db'}",
        node_id="hub",
        environment_id="test",
    )


def test_manual_message_server_controls_identity_and_projects_publish(tmp_path: Path) -> None:
    publisher = FakePublisher()
    app = create_app(settings=_settings(tmp_path), bridge_publisher=publisher)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/bridge/messages",
            json={
                "envelope": {
                    "kind": "message",
                    "destination": {"kind": "room", "id": "planning"},
                    "body": {"instruction": "Review this plan"},
                    "work_id": "work-1",
                    "extensions": {"example.audit": {"required": True}},
                }
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["message"]["status"] == "published"
        assert body["envelope"]["message_id"].startswith("msg-")
        assert body["envelope"]["correlation_id"].startswith("corr-")
        assert body["envelope"]["sender"] == {"kind": "endpoint", "id": "catalog-user"}
        message_id = body["envelope"]["message_id"]

        assert client.get(f"/api/v1/bridge/messages/{message_id}").status_code == 200
        projected = client.get(
            "/api/v1/bridge/operations/messages", params={"work_id": "work-1"}
        ).json()
        assert projected["total"] == 1
        assert projected["items"][0]["stream_sequence"] == 42
        assert publisher.published[0][1] == "bridge.v1.room.planning"

        controlled = client.post(
            "/api/v1/bridge/messages",
            json={
                "envelope": {
                    "message_id": "chosen-by-client",
                    "sender": {"kind": "endpoint", "id": "spoofed"},
                    "kind": "message",
                    "destination": {"kind": "room", "id": "planning"},
                    "body": {"instruction": "bad"},
                }
            },
        )
        assert controlled.status_code == 422

        reply = client.post(
            "/api/v1/bridge/messages",
            json={
                "subject": "bridge.v1.result.correlation-parent",
                "envelope": {
                    "kind": "response",
                    "destination": {"kind": "role", "id": "requester"},
                    "body": {"decision": "accepted"},
                    "correlation_id": "correlation-parent",
                    "causation_id": "message-parent",
                },
            },
        )
        assert reply.status_code == 201
        assert reply.json()["envelope"]["correlation_id"] == "correlation-parent"
        assert reply.json()["envelope"]["causation_id"] == "message-parent"
        assert publisher.published[-1][1] == "bridge.v1.result.correlation-parent"


def test_execution_request_query_attempt_and_cancel(tmp_path: Path) -> None:
    publisher = FakePublisher()
    app = create_app(settings=_settings(tmp_path), bridge_publisher=publisher)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/bridge/requests",
            json={
                "request": {
                    "operation": "resume_conversation",
                    "instruction": "Continue the robot server test",
                    "target": {"kind": "node", "id": "robot-host"},
                    "conversation_id": "conv-robot",
                    "cwd": "/workspace/robot",
                    "work_id": "work-robot",
                    "parameters": {"suite": "abb_sim"},
                },
                "envelope": {"extensions": {"example.priority": "high"}},
            },
        )
        assert response.status_code == 201
        created = response.json()
        execution_id = created["execution"]["execution_id"]
        assert created["execution"]["status"] == "queued"
        assert created["execution"]["attempts"][0]["attempt_number"] == 1
        assert created["envelope"]["body"]["execution_id"] == execution_id
        assert created["envelope"]["body"]["cwd"] == "/workspace/robot"

        listing = client.get("/api/v1/bridge/executions", params={"work_id": "work-robot"}).json()
        assert listing["total"] == 1
        assert client.get(f"/api/v1/bridge/requests/{execution_id}").status_code == 200

        service = app.state.manual_bridge_service
        service.record_attempt(
            ExecutionAttempt(
                attempt_id=created["execution"]["attempts"][0]["attempt_id"],
                execution_id=execution_id,
                attempt_number=1,
                node_id="robot-host",
                status=ExecutionStatus.RUNNING,
                started_at=datetime.now(UTC),
            )
        )
        assert client.get(f"/api/v1/bridge/executions/{execution_id}").json()["status"] == "running"

        cancelled = client.post(
            f"/api/v1/bridge/executions/{execution_id}/cancel",
            json={"reason": "operator changed direction"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["execution"]["status"] == "cancelled"
        cancel_envelope, subject = publisher.published[-1]
        assert cancel_envelope.body["execution_id"] == execution_id
        assert subject == "bridge.v1.control.node.robot-host"


def test_durable_result_delivery_updates_execution_before_ack(tmp_path: Path) -> None:
    publisher = FakePublisher()
    app = create_app(settings=_settings(tmp_path), bridge_publisher=publisher)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/bridge/requests",
            json={
                "request": {
                    "operation": "new_execution",
                    "instruction": "Run durable test",
                    "target": {"kind": "capability", "id": "robot-test"},
                }
            },
        ).json()
        execution_id = created["execution"]["execution_id"]
        attempt_id = created["execution"]["attempts"][0]["attempt_id"]
        result = ExecutionResult(
            execution_id=execution_id,
            attempt_id=attempt_id,
            summary="Robot test passed",
            output={"tests": 4},
        )
        envelope = BridgeEnvelope(
            message_id=f"result-{execution_id}",
            kind=MessageKind.RESPONSE,
            sender=EndpointRef(kind=EndpointKind.NODE, id="robot-host"),
            destination=EndpointRef(kind=EndpointKind.ENDPOINT, id="catalog-user"),
            body=result.model_dump(mode="json"),
            correlation_id=created["envelope"]["correlation_id"],
            causation_id=created["envelope"]["message_id"],
        )

        class Delivery:
            subject = f"bridge.v1.result.{execution_id}"
            acked = False
            dead_lettered = False
            retried = False

            @property
            def envelope(self) -> BridgeEnvelope:
                return envelope

            async def ack(self) -> None:
                assert app.state.manual_bridge_service.get_execution(execution_id)["status"] == (
                    "succeeded"
                )
                self.acked = True

            async def dead_letter(self, *, reason: str) -> None:
                self.dead_lettered = True

            async def nak(self, *, reason: str) -> None:
                self.retried = True

        delivery = Delivery()
        worker = ExecutionResultProjectionWorker(app.state.manual_bridge_service)
        asyncio.run(worker.process(delivery))  # type: ignore[arg-type]

        assert delivery.acked is True
        assert delivery.dead_lettered is False
        execution = client.get(f"/api/v1/bridge/executions/{execution_id}").json()
        assert execution["status"] == "succeeded"
        assert execution["result"]["output"] == {"tests": 4}
        assert execution["attempts"][0]["status"] == "succeeded"


def test_codex_result_catalogs_and_attaches_conversation_to_work_and_role(
    tmp_path: Path,
) -> None:
    publisher = FakePublisher()
    app = create_app(settings=_settings(tmp_path), bridge_publisher=publisher)
    with TestClient(app) as client:
        work = client.post(
            "/api/v1/work-items",
            json={"title": "PR remediation", "status": "active"},
        ).json()
        role = client.post(
            "/api/v1/roles",
            json={
                "role_type": "worker",
                "scope": f"work:{work['work_id']}",
                "charter": "Remediate review comments",
                "authority_profile": "local-write",
                "status": "active",
            },
        ).json()
        created = client.post(
            "/api/v1/bridge/requests",
            json={
                "request": {
                    "operation": "new_execution",
                    "instruction": "Update review documents",
                    "target": {"kind": "node", "id": "local-codex"},
                    "work_id": work["work_id"],
                    "cwd": "/work/pr",
                    "parameters": {"role_id": role["role_id"]},
                }
            },
        ).json()
        execution_id = created["execution"]["execution_id"]
        result = ExecutionResult(
            execution_id=execution_id,
            attempt_id=created["execution"]["attempts"][0]["attempt_id"],
            summary="Codex SDK turn completed",
            output={
                "provider_thread_id": "thread-pr-1344",
                "cwd": "/work/pr",
                "final_response": "Review intake complete",
            },
        )
        envelope = BridgeEnvelope(
            message_id=f"result-{execution_id}",
            kind=MessageKind.RESPONSE,
            sender=EndpointRef(kind=EndpointKind.NODE, id="local-codex"),
            destination=EndpointRef(kind=EndpointKind.ENDPOINT, id="catalog-user"),
            body=result.model_dump(mode="json"),
            correlation_id=created["envelope"]["correlation_id"],
            causation_id=created["envelope"]["message_id"],
        )

        class Delivery:
            subject = f"bridge.v1.result.{execution_id}"
            acked = False

            @property
            def envelope(self) -> BridgeEnvelope:
                return envelope

            async def ack(self) -> None:
                self.acked = True

            async def dead_letter(self, *, reason: str) -> None:
                raise AssertionError(reason)

            async def nak(self, *, reason: str) -> None:
                raise AssertionError(reason)

        delivery = Delivery()
        asyncio.run(app.state.result_projection_worker.process(delivery))  # type: ignore[arg-type]

        assert delivery.acked is True
        relationships = client.get(
            "/api/v1/relationships", params={"work_item_id": work["work_id"]}
        ).json()["items"]
        contains = next(item for item in relationships if item["type"] == "contains")
        conversation = client.get(
            f"/api/v1/conversations/{contains['target']['id']}"
        ).json()
        assert conversation["provider_thread_id"] == "thread-pr-1344"
        assert conversation["cwd"] == "/work/pr"
        updated_role = client.get(f"/api/v1/roles/{role['role_id']}").json()
        assert updated_role["current_conversation_id"] == conversation["conversation_id"]
        nodes = client.get("/api/v1/nodes").json()["items"]
        assert nodes[0]["node_id"] == "local-codex"
        assert nodes[0]["reachable"] is True
        assert nodes[0]["capabilities"] == ["codex"]
        assert nodes[0]["environments"][0]["root_path"] == "/work/pr"


def test_request_validation_and_unconfigured_publisher_status(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        invalid = client.post(
            "/api/v1/bridge/requests",
            json={
                "request": {
                    "execution_id": "client-controlled",
                    "operation": "resume_conversation",
                    "instruction": "resume",
                    "target": {"kind": "node", "id": "node-1"},
                }
            },
        )
        assert invalid.status_code == 422

        response = client.post(
            "/api/v1/bridge/messages",
            json={
                "envelope": {
                    "kind": "event",
                    "destination": {"kind": "room", "id": "updates"},
                    "body": {"status": "ready"},
                }
            },
        )
        assert response.status_code == 201
        assert response.json()["message"]["status"] == "publish_failed"
        assert "not configured" in response.json()["message"]["error"]


def test_catalog_lifespan_manages_configured_transport(tmp_path: Path, monkeypatch: Any) -> None:
    instances: list[Any] = []

    class EmptySubscription:
        async def fetch(self, *, batch: int, timeout: float) -> list[Any]:
            await asyncio.sleep(60)
            return []

    class ManagedTransport(FakePublisher):
        def __init__(self, settings: Any, *, observer: Any) -> None:
            super().__init__()
            self.settings = settings
            self.observer = observer
            self.connected = False
            self.provisioned = False
            self.closed = False
            self.subscribed: list[tuple[str, str]] = []
            instances.append(self)

        async def connect(self) -> None:
            self.connected = True

        async def provision_streams(self) -> None:
            self.provisioned = True

        async def subscribe(
            self, subject: str, *, durable_name: str, ack_wait_seconds: float
        ) -> EmptySubscription:
            self.subscribed.append((subject, durable_name))
            return EmptySubscription()

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(app_module, "JetStreamTransport", ManagedTransport)
    settings = replace(
        _settings(tmp_path),
        nats_servers=("nats://broker:4222",),
        result_consumer_durable="hub-results",
    )
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/bridge/messages",
            json={
                "envelope": {
                    "kind": "event",
                    "destination": {"kind": "room", "id": "updates"},
                    "body": {"status": "ready"},
                }
            },
        )
        assert response.json()["message"]["status"] == "published"
        assert instances[0].connected is True
        assert instances[0].provisioned is True
        assert instances[0].subscribed == [
            ("bridge.v1.result.>", "hub-results"),
            ("bridge.v1.inbox.>", "catalog-collaboration-inbox-v1"),
            ("bridge.v1.capability.>", "catalog-collaboration-capability-v1"),
            ("bridge.v1.room.>", "catalog-collaboration-room-v1"),
            ("bridge.v1.event.>", "catalog-collaboration-event-v1"),
        ]
    assert instances[0].closed is True


def test_catalog_startup_fails_when_collaboration_subscription_is_denied(
    tmp_path: Path, monkeypatch: Any
) -> None:
    class DeniedTransport(FakePublisher):
        def __init__(self, settings: Any, *, observer: Any) -> None:
            self.settings = settings
            self.observer = observer

        async def connect(self) -> None:
            pass

        async def provision_streams(self) -> None:
            pass

        async def subscribe(
            self, subject: str, *, durable_name: str, ack_wait_seconds: float
        ) -> Any:
            if subject != "bridge.v1.result.>":
                raise RuntimeError("subscription permission denied")

            class Empty:
                async def fetch(self, *, batch: int, timeout: float) -> list[Any]:
                    return []

            return Empty()

        async def close(self) -> None:
            pass

    monkeypatch.setattr(app_module, "JetStreamTransport", DeniedTransport)
    settings = replace(_settings(tmp_path), nats_servers=("nats://broker:4222",))
    with (
        pytest.raises(RuntimeError, match="permission denied"),
        TestClient(create_app(settings=settings)),
    ):
        pass
