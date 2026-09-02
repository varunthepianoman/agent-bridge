from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from agent_bridge_catalog.core import AttentionStore
from agent_bridge_catalog.core_api import mount_core_api
from agent_bridge_catalog.db import AttentionRow, Database


def _app(tmp_path: Path) -> tuple[FastAPI, AttentionStore]:
    database = Database(f"sqlite:///{tmp_path / 'attention.db'}")
    database.initialize()
    attention = AttentionStore(database)
    app = FastAPI()
    app.state.attention = attention
    mount_core_api(app)
    return app, attention


def _create(
    attention: AttentionStore,
    *,
    conversation_id: str = "conversation-1",
    category: str = "status",
    kind: str = "turn_completed",
    title: str = "Completed",
) -> dict[str, Any]:
    return attention.create(
        conversation_id=conversation_id,
        category=category,
        kind=kind,
        title=title,
    )


def test_attention_cursor_delivers_fifo_without_duplicates_across_restart(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite:///{tmp_path / 'attention.db'}")
    database.initialize()
    attention = AttentionStore(database)
    created_at = datetime(2026, 9, 2, 12, tzinfo=UTC)
    with database.session() as session:
        for attention_id in ("attention-b", "attention-a", "attention-c"):
            session.add(
                AttentionRow(
                    attention_id=attention_id,
                    conversation_id="conversation-1",
                    category="status",
                    kind="turn_completed",
                    title=attention_id,
                    detail="",
                    acknowledged=False,
                    created_at=created_at,
                )
            )
        session.commit()

    first, cursor = attention.list_after(limit=2)
    assert [item["attention_id"] for item in first] == ["attention-a", "attention-b"]
    assert cursor is not None

    restarted = AttentionStore(Database(f"sqlite:///{tmp_path / 'attention.db'}"))
    second, cursor = restarted.list_after(after_cursor=cursor, limit=2)
    assert [item["attention_id"] for item in second] == ["attention-c"]
    assert cursor is not None
    retried, same_cursor = restarted.list_after(after_cursor=cursor, limit=2)
    assert retried == []
    assert same_cursor == cursor


@pytest.mark.asyncio
async def test_wait_for_attention_returns_backlog_and_applies_filters(tmp_path: Path) -> None:
    app, attention = _app(tmp_path)
    ignored = _create(attention, conversation_id="conversation-2", kind="provider_failed")
    wanted = _create(attention, conversation_id="conversation-1", kind="provider_failed")
    attention.acknowledge(ignored["attention_id"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://bridge.test/api/v1"
    ) as client:
        response = await client.post(
            "/attention/wait",
            json={
                "max_wait_seconds": 0,
                "conversation_ids": ["conversation-1"],
                "category": "status",
                "kinds": ["provider_failed"],
                "unread_only": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "received"
    assert [item["attention_id"] for item in response.json()["items"]] == [
        wanted["attention_id"]
    ]
    assert response.json()["next_cursor"]


@pytest.mark.asyncio
async def test_concurrent_attention_waiters_receive_same_new_item(tmp_path: Path) -> None:
    app, attention = _app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://bridge.test/api/v1"
    ) as client:
        payload = {"max_wait_seconds": 1, "conversation_ids": ["conversation-1"]}
        waits = [
            asyncio.create_task(client.post("/attention/wait", json=payload)) for _ in range(2)
        ]
        await asyncio.sleep(0.1)
        created = await asyncio.to_thread(_create, attention)
        responses = await asyncio.gather(*waits)

    assert all(response.status_code == 200 for response in responses)
    assert all(response.json()["status"] == "received" for response in responses)
    assert all(
        response.json()["items"][0]["attention_id"] == created["attention_id"]
        for response in responses
    )


@pytest.mark.asyncio
async def test_attention_wait_timeout_invalid_cursor_and_cancellation(tmp_path: Path) -> None:
    app, _attention = _app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://bridge.test/api/v1"
    ) as client:
        invalid = await client.post(
            "/attention/wait", json={"after_cursor": "not-a-cursor", "max_wait_seconds": 0}
        )
        assert invalid.status_code == 422
        assert invalid.json()["detail"] == "invalid attention cursor"

        waiting = asyncio.create_task(
            client.post("/attention/wait", json={"max_wait_seconds": 10})
        )
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting

        timed_out = await client.post(
            "/attention/wait", json={"max_wait_seconds": 0, "batch_limit": 1}
        )

    assert timed_out.status_code == 200
    assert timed_out.json() == {"status": "timeout", "items": [], "next_cursor": None}


@pytest.mark.asyncio
async def test_list_attention_cursor_continues_with_only_newer_items(tmp_path: Path) -> None:
    app, attention = _app(tmp_path)
    existing = _create(attention)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://bridge.test/api/v1"
    ) as client:
        listed = await client.get("/attention")
        cursor = listed.json()["next_cursor"]
        assert listed.json()["items"][0]["attention_id"] == existing["attention_id"]
        timed_out = await client.post(
            "/attention/wait", json={"after_cursor": cursor, "max_wait_seconds": 0}
        )
        newer = _create(attention, title="Newer")
        received = await client.post(
            "/attention/wait", json={"after_cursor": cursor, "max_wait_seconds": 0}
        )

    assert timed_out.json()["status"] == "timeout"
    assert timed_out.json()["next_cursor"] == cursor
    assert [item["attention_id"] for item in received.json()["items"]] == [
        newer["attention_id"]
    ]
