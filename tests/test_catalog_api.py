from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from agent_bridge_catalog.app import create_app
from agent_bridge_catalog.config import Settings


class FakeProvider:
    def __init__(self) -> None:
        self.title = "Reconnect investigation"

    async def discover(self, *, include_turns: bool = True) -> AsyncIterator[dict]:
        yield {
            "provider": "codex",
            "provider_thread_id": "thr-parent",
            "title": self.title,
            "preview": "RWS reconnect invalidates the active session",
            "transcript_text": (
                "user: Find why reconnect fails\n"
                "assistant: The generation counter invalidates stale sessions"
                if include_turns
                else ""
            ),
            "status": "idle",
            "source": "vscode",
            "cwd": "/work/t_robotics",
            "repository": "github.com/example/t_robotics",
            "branch": "feature/reconnect",
            "commit_hash": "abc123",
            "created_at": 1_750_000_000,
            "last_activity_at": 1_750_000_100,
            "raw_metadata": {"safe": "metadata"},
        }
        yield {
            "provider": "codex",
            "provider_thread_id": "thr-child",
            "title": "Reconnect audit",
            "preview": "Auditing the reconnect plan",
            "status": "idle",
            "source": "subAgent",
            "cwd": "/work/t_robotics",
            "parent_provider_thread_id": "thr-parent",
            "created_at": 1_750_000_020,
            "last_activity_at": 1_750_000_050,
        }


def make_client(tmp_path: Path, provider: FakeProvider) -> TestClient:
    settings = Settings(
        state_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'catalog.db'}",
        node_id="test-node",
        environment_id="test-environment",
    )
    return TestClient(create_app(settings=settings, provider=provider))


def test_sync_search_detail_and_relationship(tmp_path: Path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        synced = client.post("/api/v1/actions/sync", json={"include_turns": True})
        assert synced.status_code == 200
        assert synced.json() == {"discovered": 2, "imported": 2}

        result = client.get("/api/v1/search", params={"q": "generation counter"})
        assert result.status_code == 200
        assert result.json()["total"] == 1
        parent = result.json()["items"][0]
        assert parent["title"] == "Reconnect investigation"
        assert parent["repository"] == "github.com/example/t_robotics"

        all_items = client.get("/api/v1/conversations").json()["items"]
        child = next(item for item in all_items if item["provider_thread_id"] == "thr-child")
        assert child["parent_conversation_id"] == parent["conversation_id"]

        detail = client.get(f"/api/v1/conversations/{parent['conversation_id']}").json()
        assert "generation counter" in detail["transcript_text"]
        assert detail["raw_metadata"] == {"safe": "metadata"}

        subagents = client.get("/api/v1/conversations", params={"source": "subAgent"})
        assert subagents.json()["total"] == 1


def test_catalog_metadata_survives_provider_resync(tmp_path: Path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        client.post("/api/v1/actions/sync", json={})
        conversation = client.get("/api/v1/conversations").json()["items"][0]
        conversation_id = conversation["conversation_id"]

        changed = client.patch(
            f"/api/v1/conversations/{conversation_id}",
            json={
                "title": "PR 17 reconnect root cause",
                "tags": [" reconnect ", "ARCI", "reconnect"],
                "notes": "Keep this visible in the PR work item.",
                "pinned": True,
            },
        )
        assert changed.status_code == 200
        assert changed.json()["tags"] == ["ARCI", "reconnect"]

        provider.title = "Provider changed this title"
        client.post("/api/v1/actions/sync", json={})
        after = client.get(f"/api/v1/conversations/{conversation_id}").json()
        assert after["title"] == "PR 17 reconnect root cause"
        assert after["provider_title"] == "Provider changed this title"
        assert after["pinned"] is True


def test_hide_filter_and_native_resume_command(tmp_path: Path) -> None:
    with make_client(tmp_path, FakeProvider()) as client:
        client.post("/api/v1/actions/sync", json={})
        item = client.get("/api/v1/conversations").json()["items"][0]
        conversation_id = item["conversation_id"]
        client.patch(f"/api/v1/conversations/{conversation_id}", json={"hidden": True})

        assert client.get("/api/v1/conversations").json()["total"] == 1
        assert (
            client.get("/api/v1/conversations", params={"include_hidden": True}).json()["total"]
            == 2
        )

        response = client.post(
            "/api/v1/actions/resume",
            json={"conversation_id": conversation_id, "launch": False},
        )
        assert response.status_code == 200
        assert response.json()["command"] == ("codex resume thr-parent -C /work/t_robotics")
        assert response.json()["launched"] is False
