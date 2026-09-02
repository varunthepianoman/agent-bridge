from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from agent_bridge_catalog.app import create_app
from agent_bridge_catalog.config import Settings
from agent_bridge_catalog.core_api import MailboxWait, stop_listener, wait_mailbox
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
            },
        )
        assert message.status_code == 201
        assert message.json()["state"] == "published"
        assert message.json()["delivery_strategy"] == "mailbox"
        assert message.json()["delivery_route"] is None
        assert publisher.envelopes[0].destination.id == conversation_id
        assert publisher.envelopes[0].delivery.strategy == "mailbox"

        room = client.post("/api/v1/rooms", json={"name": "socket-debug"}).json()
        assert (
            client.put(
                f"/api/v1/rooms/{room['room_id']}/members/{conversation_id}",
                json={"delivery_mode": "notify"},
            ).status_code
            == 200
        )
        assert client.get("/api/v1/nats/summary").json()["broker"]["status"] == "healthy"


def test_foreground_mailbox_wait_completion_and_correlated_reply(tmp_path: Path) -> None:
    publisher = Publisher()
    app = create_app(settings=settings(tmp_path), provider=Provider(), bridge_publisher=publisher)
    with TestClient(app) as client:
        client.post("/api/v1/reconciliation")
        candidates = client.get("/api/v1/conversations/candidates").json()["items"]
        selected = client.post(
            "/api/v1/conversations/import",
            json={"conversation_ids": [item["conversation_id"] for item in candidates]},
        ).json()["items"]
        source, target = [item["conversation_id"] for item in selected]
        sent = client.post(
            "/api/v1/messages",
            json={
                "body": "Please inspect this",
                "target_conversation_id": target,
                "source_conversation_id": source,
                "actor_kind": "agent",
                "operation": "request",
                "correlation_id": "correlation-api-test",
            },
        ).json()
        app.state.mailbox.enqueue(sent["message_id"], [target])

        received = client.post(
            f"/api/v1/mailbox/{target}/wait",
            json={"max_wait_seconds": 0, "batch_limit": 10},
        )
        assert received.status_code == 200
        assert received.json()["status"] == "received"
        assert received.json()["items"][0]["body"] == "Please inspect this"
        assert received.json()["items"][0]["state"] == "received"

        completed = client.post(
            f"/api/v1/messages/{sent['message_id']}/complete",
            json={
                "conversation_id": target,
                "outcome": "succeeded",
                "detail": "verified",
                "reply_body": "Inspection complete",
            },
        )
        assert completed.status_code == 200
        assert completed.json()["state"] == "succeeded"
        reply = app.state.messages.get(completed.json()["reply_message_id"])
        assert reply["target_conversation_id"] == source
        assert reply["source_conversation_id"] == target
        assert reply["correlation_id"] == "correlation-api-test"
        assert reply["causation_id"] == sent["message_id"]

        repeated = client.post(
            f"/api/v1/messages/{sent['message_id']}/complete",
            json={
                "conversation_id": target,
                "outcome": "succeeded",
                "detail": "verified",
                "reply_body": "Inspection complete",
            },
        )
        assert repeated.status_code == 200
        assert repeated.json()["reply_message_id"] == completed.json()["reply_message_id"]


def test_requested_acknowledgement_and_receipt_wait_lifecycle(tmp_path: Path) -> None:
    app = create_app(settings=settings(tmp_path), provider=Provider(), bridge_publisher=Publisher())
    with TestClient(app) as client:
        client.post("/api/v1/reconciliation")
        candidates = client.get("/api/v1/conversations/candidates").json()["items"]
        selected = client.post(
            "/api/v1/conversations/import",
            json={"conversation_ids": [item["conversation_id"] for item in candidates]},
        ).json()["items"]
        source, target = [item["conversation_id"] for item in selected]
        sent = client.post(
            "/api/v1/messages",
            json={
                "body": "Please acknowledge before the long inspection",
                "target_conversation_id": target,
                "source_conversation_id": source,
                "actor_kind": "agent",
                "acknowledgement_requested": True,
            },
        ).json()
        app.state.mailbox.enqueue(sent["message_id"], [target])

        pending = client.post(
            f"/api/v1/messages/{sent['message_id']}/wait-receipt",
            json={
                "source_conversation_id": source,
                "until": "claimed",
                "timeout_seconds": 0,
            },
        ).json()
        assert pending["status"] == "timeout"
        assert pending["message"]["state"] in {"published", "pending_broker"}
        assert pending["receipt"]["state"] == "pending"
        assert "recipient_listener" in pending
        assert "recipient_node_reachable" in pending

        claimed_batch = client.post(
            f"/api/v1/mailbox/{target}/wait",
            json={"max_wait_seconds": 0, "batch_limit": 1},
        ).json()
        claimed = claimed_batch["items"][0]
        assert claimed["claimed_at"] == claimed["received_at"]
        assert claimed["attempt"] == 1
        claimed_inbox = client.get(
            f"/api/v1/mailbox/{target}", params={"state": "claimed"}
        ).json()
        assert [item["message_id"] for item in claimed_inbox["items"]] == [sent["message_id"]]

        claimed_receipt = client.post(
            f"/api/v1/messages/{sent['message_id']}/wait-receipt",
            json={
                "source_conversation_id": source,
                "until": "claimed",
                "timeout_seconds": 0,
            },
        ).json()
        assert claimed_receipt["status"] == "reached"
        claimed_revision = claimed_receipt["receipt"]["revision"]
        assert claimed_receipt["receipt"]["claimed_revision"] == claimed_revision

        acknowledged = client.post(
            f"/api/v1/messages/{sent['message_id']}/acknowledge",
            json={"conversation_id": target, "detail": "inspection started"},
        )
        assert acknowledged.status_code == 200
        assert acknowledged.json()["acknowledged_at"] is not None
        assert acknowledged.json()["acknowledgement_detail"] == "inspection started"
        assert (
            client.post(
                f"/api/v1/messages/{sent['message_id']}/acknowledge",
                json={"conversation_id": target, "detail": "inspection started"},
            ).json()
            == acknowledged.json()
        )

        ack_receipt = client.post(
            f"/api/v1/messages/{sent['message_id']}/wait-receipt",
            json={
                "source_conversation_id": source,
                "until": "acknowledged",
                "timeout_seconds": 0,
                "after_revision": claimed_revision,
            },
        ).json()
        assert ack_receipt["status"] == "reached"
        ack_revision = ack_receipt["receipt"]["revision"]
        assert ack_receipt["receipt"]["acknowledged_revision"] == ack_revision
        assert ack_revision > claimed_revision

        old_claim = client.post(
            f"/api/v1/messages/{sent['message_id']}/wait-receipt",
            json={
                "source_conversation_id": source,
                "until": "claimed",
                "timeout_seconds": 0,
                "after_revision": claimed_revision,
            },
        ).json()
        assert old_claim["status"] == "timeout"

        stale_wait = client.post(
            f"/api/v1/messages/{sent['message_id']}/wait-receipt",
            json={
                "source_conversation_id": source,
                "until": "acknowledged",
                "timeout_seconds": 0,
                "after_revision": ack_revision,
            },
        ).json()
        assert stale_wait["status"] == "timeout"

        completed = client.post(
            f"/api/v1/messages/{sent['message_id']}/complete",
            json={"conversation_id": target, "outcome": "succeeded"},
        )
        assert completed.status_code == 200
        old_ack = client.post(
            f"/api/v1/messages/{sent['message_id']}/wait-receipt",
            json={
                "source_conversation_id": source,
                "until": "acknowledged",
                "timeout_seconds": 0,
                "after_revision": ack_revision,
            },
        ).json()
        assert old_ack["status"] == "timeout"
        terminal = client.post(
            f"/api/v1/messages/{sent['message_id']}/wait-receipt",
            json={
                "source_conversation_id": source,
                "until": "terminal",
                "timeout_seconds": 0,
                "after_revision": ack_revision,
            },
        ).json()
        assert terminal["status"] == "reached"
        attention_kinds = [item["kind"] for item in client.get("/api/v1/attention").json()["items"]]
        assert attention_kinds.count("mailbox_acknowledged") == 1
        assert attention_kinds.count("mailbox_terminal") == 1


def test_receipt_validation_and_immediate_completion_notification(tmp_path: Path) -> None:
    app = create_app(settings=settings(tmp_path), provider=Provider(), bridge_publisher=Publisher())
    with TestClient(app) as client:
        client.post("/api/v1/reconciliation")
        candidates = client.get("/api/v1/conversations/candidates").json()["items"]
        selected = client.post(
            "/api/v1/conversations/import",
            json={"conversation_ids": [item["conversation_id"] for item in candidates]},
        ).json()["items"]
        source, target = [item["conversation_id"] for item in selected]

        missing_source = client.post(
            "/api/v1/messages",
            json={
                "body": "receipt please",
                "target_conversation_id": target,
                "acknowledgement_requested": True,
            },
        )
        assert missing_source.status_code == 422
        room_receipt = client.post(
            "/api/v1/messages",
            json={
                "body": "receipt please",
                "room_id": "room-any",
                "source_conversation_id": source,
                "acknowledgement_requested": True,
            },
        )
        assert room_receipt.status_code == 422

        sent = client.post(
            "/api/v1/messages",
            json={
                "body": "quick check",
                "target_conversation_id": target,
                "source_conversation_id": source,
                "acknowledgement_requested": True,
            },
        ).json()
        app.state.mailbox.enqueue(sent["message_id"], [target])
        before_claim = client.post(
            f"/api/v1/messages/{sent['message_id']}/acknowledge",
            json={"conversation_id": target},
        )
        assert before_claim.status_code == 409
        client.post(
            f"/api/v1/mailbox/{target}/wait",
            json={"max_wait_seconds": 0, "batch_limit": 1},
        )
        completed = client.post(
            f"/api/v1/messages/{sent['message_id']}/complete",
            json={"conversation_id": target, "outcome": "succeeded"},
        ).json()
        assert completed["acknowledged_at"] is not None

        attention_kinds = [item["kind"] for item in client.get("/api/v1/attention").json()["items"]]
        assert attention_kinds.count("mailbox_acknowledged") == 0
        assert attention_kinds.count("mailbox_terminal") == 1

        wrong_sender = client.post(
            f"/api/v1/messages/{sent['message_id']}/wait-receipt",
            json={
                "source_conversation_id": target,
                "until": "terminal",
                "timeout_seconds": 0,
            },
        )
        assert wrong_sender.status_code == 403


def test_multiple_receipt_waiters_do_not_compete_for_listener_ownership(
    tmp_path: Path, monkeypatch
) -> None:
    app = create_app(settings=settings(tmp_path), provider=Provider(), bridge_publisher=Publisher())
    with TestClient(app) as client:
        client.post("/api/v1/reconciliation")
        candidates = client.get("/api/v1/conversations/candidates").json()["items"]
        selected = client.post(
            "/api/v1/conversations/import",
            json={"conversation_ids": [item["conversation_id"] for item in candidates]},
        ).json()["items"]
        source, target = [item["conversation_id"] for item in selected]
        sent = client.post(
            "/api/v1/messages",
            json={
                "body": "long inspection",
                "target_conversation_id": target,
                "source_conversation_id": source,
                "acknowledgement_requested": True,
            },
        ).json()
        app.state.mailbox.enqueue(sent["message_id"], [target])
        client.post(
            f"/api/v1/mailbox/{target}/wait",
            json={"max_wait_seconds": 0, "batch_limit": 1},
        )

        original_get_delivery = app.state.mailbox.get_delivery
        waiter_count = 0
        waiter_lock = Lock()
        both_waiting = Event()

        def observed_get_delivery(message_id: str, conversation_id: str):
            nonlocal waiter_count
            delivery = original_get_delivery(message_id, conversation_id)
            if delivery is not None and delivery["acknowledged_at"] is None:
                with waiter_lock:
                    waiter_count += 1
                    if waiter_count >= 2:
                        both_waiting.set()
            return delivery

        monkeypatch.setattr(app.state.mailbox, "get_delivery", observed_get_delivery)
        wait_payload = {
            "source_conversation_id": source,
            "until": "acknowledged",
            "timeout_seconds": 2,
        }
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    client.post,
                    f"/api/v1/messages/{sent['message_id']}/wait-receipt",
                    json=wait_payload,
                )
                for _ in range(2)
            ]
            assert both_waiting.wait(timeout=1)
            assert app.state.mailbox.get_listener(target) is None
            acknowledged = client.post(
                f"/api/v1/messages/{sent['message_id']}/acknowledge",
                json={"conversation_id": target, "detail": "started"},
            )
            assert acknowledged.status_code == 200
            results = [future.result(timeout=2) for future in futures]

        assert [result.status_code for result in results] == [200, 200]
        assert [result.json()["status"] for result in results] == ["reached", "reached"]


@pytest.mark.asyncio
async def test_cancelled_mailbox_wait_releases_only_its_listener(tmp_path: Path) -> None:
    app = create_app(settings=settings(tmp_path), provider=Provider(), bridge_publisher=Publisher())
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://bridge.test"
    ) as client:
        await client.post("/api/v1/reconciliation")
        candidates = (await client.get("/api/v1/conversations/candidates")).json()["items"]
        selected = (
            await client.post(
                "/api/v1/conversations/import",
                json={"conversation_ids": [item["conversation_id"] for item in candidates]},
            )
        ).json()["items"]
        first, second = [item["conversation_id"] for item in selected]

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        def request() -> Request:
            return Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/",
                    "headers": [],
                    "query_string": b"",
                    "app": app,
                },
                receive,
            )

        payload = MailboxWait(max_wait_seconds=10, batch_limit=1)
        first_wait = asyncio.create_task(wait_mailbox(first, payload, request()))
        second_wait = asyncio.create_task(wait_mailbox(second, payload, request()))
        while app.state.mailbox.get_listener(first) is None:
            await asyncio.sleep(0.01)
        while app.state.mailbox.get_listener(second) is None:
            await asyncio.sleep(0.01)

        first_wait.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_wait
        assert app.state.mailbox.get_listener(first) is None
        assert app.state.mailbox.get_listener(second) is not None

        stopped = stop_listener(second, request())
        assert stopped["status"] == "stop_requested"
        assert (await second_wait)["status"] == "stopped"
        assert app.state.mailbox.get_listener(second) is None


def test_removed_orchestration_apis_are_absent(tmp_path: Path) -> None:
    with TestClient(create_app(settings=settings(tmp_path), provider=Provider())) as client:
        for path in ("/api/v1/work-items", "/api/v1/roles", "/api/v1/coordinator/intake"):
            assert client.get(path).status_code == 404
