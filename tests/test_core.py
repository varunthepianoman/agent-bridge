from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from agent_bridge_catalog.app import create_app
from agent_bridge_catalog.config import Settings
from agent_bridge_catalog.db import Database
from agent_bridge_catalog.repository import CatalogRepository


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


def test_discovery_is_candidate_only_and_selection_assigns_stable_number(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_NATIVE_LAUNCH", "0")
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
        assert all(item["native_launch_enabled"] is True for item in candidates)
        assert all(item["native_launch_enabled"] is True for item in selected)
        child = next(item for item in selected if item["provider_thread_id"] == "child")
        assert child["conversation_kind"] == "native_subagent"
        assert child["delivery_mode"] == "catalog_only"
        assert child["display_name"].startswith("Chat ")


def test_auto_add_setting_selects_only_future_discoveries_including_subagents(
    tmp_path: Path,
) -> None:
    app = create_app(settings=settings(tmp_path), provider=Provider())
    with TestClient(app) as client:
        client.post("/api/v1/reconciliation")
        assert client.get("/api/v1/conversations").json()["total"] == 0

        updated = client.patch(
            "/api/v1/settings", json={"auto_add_new_chats": True}
        ).json()
        assert updated == {"auto_add_new_chats": True}

        database = app.state.database
        repository = CatalogRepository(database)
        new_row = repository.upsert_discovered(
            {
                "provider": "claude",
                "provider_thread_id": "future-child",
                "parent_thread_id": "future-parent",
                "title": "Future subagent",
                "transcript_text": "assistant: checking",
            },
            node_id="hub",
            environment_id="host",
            select_if_new=app.state.preferences.auto_add_new_chats(),
        )

        assert new_row.selected
        assert new_row.conversation_number == 1
        assert new_row.conversation_kind == "native_subagent"
        assert client.get("/api/v1/conversations").json()["total"] == 1
        assert client.get("/api/v1/conversations/candidates").json()["total"] == 2


def test_native_urls_are_provider_specific(tmp_path: Path) -> None:
    app = create_app(settings=settings(tmp_path), provider=Provider())
    with TestClient(app) as client:
        repository = app.state.repository
        codex = repository.upsert_discovered(
            {"provider": "codex", "provider_thread_id": "codex-id", "cwd": "/work/repo"},
            node_id="hub",
            environment_id="host",
            select_if_new=True,
        )
        claude = repository.upsert_discovered(
            {"provider": "claude", "provider_thread_id": "claude-id", "cwd": "/work/repo"},
            node_id="hub",
            environment_id="host",
            select_if_new=True,
        )

        codex_payload = client.get(f"/api/v1/conversations/{codex.conversation_id}").json()
        claude_payload = client.get(f"/api/v1/conversations/{claude.conversation_id}").json()
        assert codex_payload["native_url"] == "codex://threads/codex-id"
        assert claude_payload["native_url"] == "claude://code/new?folder=%2Fwork%2Frepo"


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


def test_metadata_only_sync_preserves_transcript_and_derives_bounded_alias(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'catalog.db'}")
    database.initialize()
    repository = CatalogRepository(database)
    item = {
        "provider": "codex",
        "provider_thread_id": "unnamed",
        "title": None,
        "preview": "A descriptive first prompt that should become the catalog alias",
        "transcript_text": "",
    }
    row = repository.upsert_discovered(
        item,
        node_id="desktop",
        environment_id="host",
        transcript_included=False,
    )
    repository.select([row.conversation_id])
    item["transcript_text"] = "user: hello\nassistant: hi"
    repository.upsert_discovered(item, node_id="desktop", environment_id="host")
    item["transcript_text"] = ""
    row = repository.upsert_discovered(
        item,
        node_id="desktop",
        environment_id="host",
        transcript_included=False,
    )

    assert row.alias == "A descriptive first prompt that should become the catalog alias"
    assert row.transcript_text == "user: hello\nassistant: hi"


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
            json={
                "body": "Check the server side too",
                "target_conversation_id": conversation_id,
                "delivery_strategy": "steer-or-queue",
            },
        )
        assert message.status_code == 201
        assert message.json()["state"] == "published"
        assert message.json()["delivery_strategy"] == "steer-or-queue"
        assert message.json()["delivery_route"] is None
        assert publisher.envelopes[0].destination.id == conversation_id
        assert publisher.envelopes[0].delivery.strategy == "steer-or-queue"

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
