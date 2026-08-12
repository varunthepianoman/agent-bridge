from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

from agent_bridge_catalog.app import create_app
from agent_bridge_catalog.collaboration import CollaborationStore
from agent_bridge_catalog.config import Settings
from agent_bridge_catalog.db import CollaborationMessageRow
from agent_bridge_catalog.result_projection_worker import ExecutionResultProjectionWorker
from agent_bridge_protocol import BridgeEnvelope, EndpointKind, EndpointRef, MessageKind


@dataclass
class _Ack:
    stream: str = "BRIDGE_EVENTS_V1"
    sequence: int = 1
    duplicate: bool = False


class _Publisher:
    def __init__(self) -> None:
        self.items: list[tuple[BridgeEnvelope, str | None]] = []

    async def publish(self, envelope: BridgeEnvelope, *, subject: str | None = None) -> _Ack:
        self.items.append((envelope, subject))
        return _Ack(sequence=len(self.items))


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        state_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'collaboration.db'}",
        node_id="hub",
        environment_id="test",
    )


def _register_workers(client: TestClient) -> None:
    for suffix in ("a", "b"):
        response = client.post(
            "/api/v1/collaboration/endpoints",
            json={
                "endpoint_id": f"runner-{suffix}",
                "display_name": f"Runner {suffix.upper()}",
                "address": {"kind": "node", "id": f"node-{suffix}"},
                "capabilities": ["robot-test"],
                "extensions": {"vendor.future": {"weight": suffix}},
            },
        )
        assert response.status_code == 201


def test_capability_competes_room_is_durable_and_fanout_is_explicit(tmp_path: Path) -> None:
    publisher = _Publisher()
    app = create_app(settings=_settings(tmp_path), bridge_publisher=publisher)
    with TestClient(app) as client:
        _register_workers(client)
        room = client.post(
            "/api/v1/collaboration/rooms",
            json={
                "room_id": "planning",
                "name": "Planning",
                "members": [
                    {"kind": "node", "id": "node-a"},
                    {"kind": "node", "id": "node-b"},
                ],
                "extensions": {"future.room": True},
            },
        )
        assert room.status_code == 201

        capability = client.post(
            "/api/v1/collaboration/messages",
            json={
                "operation": "capability",
                "capability": "robot-test",
                "body": {"instruction": "claim once"},
            },
        )
        assert capability.status_code == 201
        assert len(publisher.items) == 1
        assert publisher.items[-1][0].destination == EndpointRef(
            kind=EndpointKind.CAPABILITY, id="robot-test"
        )

        durable_room = client.post(
            "/api/v1/collaboration/messages",
            json={
                "operation": "direct",
                "room_id": "planning",
                "body": {"status": "observe independently"},
            },
        )
        assert durable_room.status_code == 201
        assert len(publisher.items) == 2
        assert publisher.items[-1][0].destination == EndpointRef(
            kind=EndpointKind.ROOM, id="planning"
        )

        fanout = client.post(
            "/api/v1/collaboration/messages",
            json={
                "operation": "fanout",
                "capability": "robot-test",
                "body": {"notice": "send to all"},
            },
        )
        assert fanout.status_code == 201
        assert len(publisher.items) == 4


def test_planner_auditor_convention_and_correlation_history(tmp_path: Path) -> None:
    publisher = _Publisher()
    app = create_app(settings=_settings(tmp_path), bridge_publisher=publisher)
    with TestClient(app) as client:
        proposal = client.post(
            "/api/v1/collaboration/messages",
            json={
                "operation": "proposal",
                "destinations": [{"kind": "role", "id": "auditor"}],
                "body": {"plan": ["implement", "test"]},
                "extensions": {"future.review": {"round": 1}},
            },
        ).json()
        critique = client.post(
            "/api/v1/collaboration/messages",
            json={
                "operation": "critique",
                "destinations": [{"kind": "role", "id": "planner"}],
                "body": {"finding": "add recovery test"},
                "causation_id": proposal["collaboration_id"],
            },
        )
        assert critique.status_code == 201
        critique_body = critique.json()
        assert critique_body["correlation_id"] == proposal["correlation_id"]
        assert publisher.items[-1][0].correlation_id == proposal["correlation_id"]
        assert publisher.items[-1][0].causation_id == proposal["collaboration_id"]

        invalid = client.post(
            "/api/v1/collaboration/messages",
            json={
                "operation": "revision",
                "destinations": [{"kind": "role", "id": "auditor"}],
                "body": {"plan": "wrong parent"},
                "causation_id": proposal["collaboration_id"],
            },
        )
        assert invalid.status_code == 422

        history = client.get(
            "/api/v1/collaboration/messages",
            params={"correlation_id": proposal["correlation_id"]},
        ).json()
        assert history["total"] == 2
        assert history["items"][1]["extensions"] == {"future.review": {"round": 1}}


def test_inbound_projection_is_idempotent_and_preserves_extensions(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path), bridge_publisher=_Publisher())
    with TestClient(app):
        store: CollaborationStore = app.state.collaboration_store
        envelope = BridgeEnvelope(
            message_id="msg-inbound",
            kind=MessageKind.REQUEST,
            sender=EndpointRef(kind=EndpointKind.NODE, id="remote"),
            destination=EndpointRef(kind=EndpointKind.ROLE, id="worker"),
            body={"question": "status?"},
            correlation_id="corr-inbound",
            extensions={"unknown.namespace": {"value": 7}},
        )
        first = store.ingest_envelope(envelope)
        second = store.ingest_envelope(envelope)
        assert first == second
        assert first.extensions == {"unknown.namespace": {"value": 7}}
        with app.state.database.session() as session:
            assert session.query(CollaborationMessageRow).count() == 1


def test_generic_result_reply_routes_to_collaboration_not_execution_dlq(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path), bridge_publisher=_Publisher())
    with TestClient(app):
        envelope = BridgeEnvelope(
            message_id="generic-reply",
            kind=MessageKind.RESPONSE,
            sender=EndpointRef(kind=EndpointKind.ROLE, id="auditor"),
            destination=EndpointRef(kind=EndpointKind.ROLE, id="planner"),
            body={"decision": "accepted", "status": "accepted"},
            correlation_id="review-1",
            causation_id="proposal-1",
        )

        class _Delivery:
            subject = "bridge.v1.result.review-1"

            def __init__(self) -> None:
                self.envelope = envelope
                self.acked = False
                self.dead = False

            async def ack(self) -> None:
                self.acked = True

            async def nak(self, *, reason: str) -> None:
                raise AssertionError(reason)

            async def dead_letter(self, *, reason: str) -> None:
                self.dead = True

        worker: ExecutionResultProjectionWorker = app.state.result_projection_worker
        delivery = _Delivery()
        import asyncio

        asyncio.run(worker.process(delivery))  # type: ignore[arg-type]
        assert delivery.acked is True
        assert delivery.dead is False
        assert app.state.collaboration_store.get_message("generic-reply") is not None


def test_native_subagent_family_is_visible_but_not_mediated(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path), bridge_publisher=_Publisher())
    with TestClient(app) as client:
        repository = app.state.repository
        root = repository.upsert_discovered(
            {"provider_thread_id": "root", "title": "Root"},
            node_id="hub",
            environment_id="test",
        )
        child = repository.upsert_discovered(
            {
                "provider_thread_id": "child",
                "parent_provider_thread_id": "root",
                "title": "Native child",
            },
            node_id="hub",
            environment_id="test",
        )
        repository.resolve_parents()
        response = client.get("/api/v1/collaboration/native-subagents")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["conversation_id"] == child.conversation_id
        assert item["root_conversation_id"] == root.conversation_id
        assert item["addressable"] is False
        assert "address" not in item
        with app.state.database.session() as session:
            assert session.query(CollaborationMessageRow).count() == 0


def test_unknown_cross_work_relationship_roundtrips_in_topology(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path), bridge_publisher=_Publisher())
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/relationships",
            json={
                "relationship_id": "rel-cross-work",
                "source": {"kind": "endpoint", "id": "work-a"},
                "target": {"kind": "endpoint", "id": "work-b"},
                "type": "future_dependency_kind",
                "metadata": {"reason": "shared API"},
                "extensions": {"vendor.edge": {"strength": 0.5}},
            },
        )
        assert created.status_code == 201
        edge = client.get("/api/v1/collaboration/topology").json()["edges"][0]
        assert edge["type"] == "future_dependency_kind"
        assert edge["extensions"] == {"vendor.edge": {"strength": 0.5}}
