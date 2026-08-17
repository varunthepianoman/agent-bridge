from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from agent_bridge_catalog.app import create_app
from agent_bridge_catalog.config import Settings


class Provider:
    async def discover(self, *, include_turns: bool = True) -> AsyncIterator[Any]:
        del include_turns
        for item in (
            {
                "provider": "codex",
                "provider_thread_id": "root",
                "title": "Fix the socket",
                "preview": "Investigating a cross-machine socket issue",
                "transcript_text": "user: investigate\nassistant: found the race",
                "cwd": "/tmp",
                "status": "idle",
            },
            {
                "provider": "codex",
                "provider_thread_id": "child",
                "parent_thread_id": "root",
                "title": "Adversarial review",
                "source_kind": "subAgent",
                "cwd": "/tmp",
                "status": "completed",
            },
        ):
            yield SimpleNamespace(**item)

    async def close(self) -> None:
        pass


class Publisher:
    connected = True

    def __init__(self) -> None:
        self.envelopes: list[Any] = []

    async def publish(self, envelope: Any, *, subject: str | None = None) -> object:
        del subject
        self.envelopes.append(envelope)
        return object()

    async def diagnostics(self) -> dict[str, Any]:
        return {"status": "healthy", "connected": True}


def settings(tmp_path: Path) -> Settings:
    return Settings(
        state_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'core.db'}",
        node_id="hub",
        environment_id="host",
        discovery_interval_seconds=3600,
    )


def test_discovery_is_candidate_only_and_selection_assigns_stable_number(tmp_path: Path) -> None:
    app = create_app(settings=settings(tmp_path), provider=Provider())
    with TestClient(app) as client:
        assert client.post("/api/v1/reconciliation").status_code == 200
        assert client.get("/api/v1/conversations").json()["total"] == 0
        candidates = client.get("/api/v1/conversations/candidates").json()["items"]
        selected = client.post(
            "/api/v1/conversations/import",
            json={"conversation_ids": [item["conversation_id"] for item in candidates]},
        ).json()["items"]

        assert {item["conversation_number"] for item in selected} == {1, 2}
        child = next(item for item in selected if item["provider_thread_id"] == "child")
        assert child["conversation_kind"] == "native_subagent"
        assert child["delivery_mode"] == "catalog_only"
        assert child["display_name"].startswith("Chat ")


def test_alias_tracks_real_provider_title_changes_and_human_edits(tmp_path: Path) -> None:
    app = create_app(settings=settings(tmp_path), provider=Provider())
    with TestClient(app) as client:
        client.post("/api/v1/reconciliation")
        candidate = client.get("/api/v1/conversations/candidates").json()["items"][0]
        conversation_id = candidate["conversation_id"]
        client.post("/api/v1/conversations/import", json={"conversation_ids": [conversation_id]})
        changed = client.patch(
            f"/api/v1/conversations/{conversation_id}", json={"alias": "Socket work"}
        ).json()
        assert changed["alias"] == "Socket work"
        assert changed["alias_updated_by"] == "human"


def test_messages_rooms_attention_and_nats_diagnostics(tmp_path: Path) -> None:
    publisher = Publisher()
    app = create_app(settings=settings(tmp_path), provider=Provider(), bridge_publisher=publisher)
    with TestClient(app) as client:
        client.post("/api/v1/reconciliation")
        candidate = client.get("/api/v1/conversations/candidates").json()["items"][0]
        conversation_id = candidate["conversation_id"]
        client.post("/api/v1/conversations/import", json={"conversation_ids": [conversation_id]})
        message = client.post(
            "/api/v1/messages",
            json={"body": "Check the server side too", "target_conversation_id": conversation_id},
        )
        assert message.status_code == 201
        assert message.json()["state"] == "published"
        assert publisher.envelopes[0].destination.id == conversation_id

        room = client.post("/api/v1/rooms", json={"name": "socket-debug"}).json()
        assert (
            client.put(
                f"/api/v1/rooms/{room['room_id']}/members/{conversation_id}",
                json={"delivery_mode": "notify"},
            ).status_code
            == 200
        )
        assert client.get("/api/v1/nats/summary").json()["broker"]["status"] == "healthy"


def test_removed_orchestration_apis_are_absent(tmp_path: Path) -> None:
    with TestClient(create_app(settings=settings(tmp_path), provider=Provider())) as client:
        for path in ("/api/v1/work-items", "/api/v1/roles", "/api/v1/coordinator/intake"):
            assert client.get(path).status_code == 404
