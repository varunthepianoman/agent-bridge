from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_bridge_catalog.core import MailboxStore
from agent_bridge_catalog.db import ConversationMessageRow, Database, MailboxDeliveryRow
from agent_bridge_catalog.repository import CatalogRepository


def _setup(tmp_path: Path) -> tuple[Database, MailboxStore, list[str]]:
    database = Database(f"sqlite:///{tmp_path / 'mailbox.db'}")
    database.initialize()
    repository = CatalogRepository(database)
    conversations = [
        repository.upsert_discovered(
            {"provider": "codex", "provider_thread_id": thread_id},
            node_id="hub",
            environment_id="host",
            select_if_new=True,
        ).conversation_id
        for thread_id in ("recipient-a", "recipient-b")
    ]
    now = datetime.now(UTC)
    with database.session() as session:
        session.add(
            ConversationMessageRow(
                message_id="message-1",
                correlation_id="correlation-1",
                source_conversation_id=conversations[1],
                room_id="room-1",
                actor_kind="agent",
                operation="request",
                body="inspect the mailbox",
                delivery_strategy="mailbox",
                state="published",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    return database, MailboxStore(database), conversations


def test_enqueue_fans_out_per_recipient_and_joins_message_content(tmp_path: Path) -> None:
    _database, mailbox, conversations = _setup(tmp_path)

    deliveries = mailbox.enqueue("message-1", [*conversations, conversations[0]])

    assert [item["recipient_conversation_id"] for item in deliveries] == conversations
    assert all(item["state"] == "pending" for item in deliveries)
    assert all(item["body"] == "inspect the mailbox" for item in deliveries)
    assert all(item["correlation_id"] == "correlation-1" for item in deliveries)
    assert mailbox.enqueue("message-1", conversations) == deliveries
    assert [event["event_kind"] for event in mailbox.list_events(message_id="message-1")] == [
        "created",
        "created",
    ]


def test_listener_fence_claim_and_completion_survive_listener_release(tmp_path: Path) -> None:
    _database, mailbox, conversations = _setup(tmp_path)
    recipient = conversations[0]
    mailbox.enqueue("message-1", [recipient])
    listener = mailbox.acquire_listener(recipient, listener_id="listener-a")

    with pytest.raises(ValueError, match="active"):
        mailbox.acquire_listener(recipient, listener_id="listener-b")
    received = mailbox.receive_pending(
        recipient,
        listener_id="listener-a",
        fencing_token=listener["fencing_token"],
    )
    assert [item["message_id"] for item in received] == ["message-1"]
    assert mailbox.receive_pending(
        recipient,
        listener_id="listener-a",
        fencing_token=listener["fencing_token"],
    ) == []

    assert mailbox.release_listener(
        recipient,
        listener_id="listener-a",
        fencing_token=listener["fencing_token"],
    )
    completed = mailbox.complete(
        "message-1",
        recipient,
        outcome="succeeded",
        detail="done",
        listener_id="listener-a",
        fencing_token=listener["fencing_token"],
        reply_message_id="reply-1",
    )
    assert completed["state"] == "succeeded"
    assert completed["reply_message_id"] == "reply-1"
    assert mailbox.get_delivery("message-1", recipient) == completed

    assert mailbox.complete(
        "message-1",
        recipient,
        outcome="succeeded",
        detail="ignored on idempotent retry",
        listener_id="listener-a",
        fencing_token=listener["fencing_token"],
        reply_message_id="reply-1",
    ) == completed
    with pytest.raises(ValueError, match="different listener claim"):
        mailbox.complete(
            "message-1",
            recipient,
            outcome="succeeded",
            detail="done",
            listener_id="listener-stale",
            fencing_token=listener["fencing_token"],
            reply_message_id="reply-1",
        )
    with pytest.raises(ValueError, match="conflicting"):
        mailbox.complete(
            "message-1",
            recipient,
            outcome="succeeded",
            detail="done",
            listener_id="listener-a",
            fencing_token=listener["fencing_token"],
            reply_message_id="reply-2",
        )


def test_reacquired_listener_fences_stale_claims(tmp_path: Path) -> None:
    _database, mailbox, conversations = _setup(tmp_path)
    recipient = conversations[0]
    mailbox.enqueue("message-1", [recipient])
    first = mailbox.acquire_listener(recipient, listener_id="listener-a")
    mailbox.release_listener(
        recipient,
        listener_id=first["listener_id"],
        fencing_token=first["fencing_token"],
    )
    second = mailbox.acquire_listener(recipient, listener_id="listener-b")

    assert second["fencing_token"] == first["fencing_token"] + 1
    with pytest.raises(ValueError, match="stale"):
        mailbox.receive_pending(
            recipient,
            listener_id=first["listener_id"],
            fencing_token=first["fencing_token"],
        )


def test_stale_received_attention_is_claimed_exactly_once(tmp_path: Path) -> None:
    database, mailbox, conversations = _setup(tmp_path)
    recipient = conversations[0]
    mailbox.enqueue("message-1", [recipient])
    listener = mailbox.acquire_listener(recipient, listener_id="listener-a")
    mailbox.receive_pending(
        recipient,
        listener_id=listener["listener_id"],
        fencing_token=listener["fencing_token"],
    )
    old = datetime.now(UTC) - timedelta(minutes=10)
    with database.session() as session:
        row = session.get(MailboxDeliveryRow, ("message-1", recipient))
        assert row is not None
        row.received_at = old
        session.commit()

    claimed = mailbox.claim_stale_received(older_than=datetime.now(UTC) - timedelta(minutes=5))

    assert [item["message_id"] for item in claimed] == ["message-1"]
    assert claimed[0]["attention_emitted_at"] is not None
    assert mailbox.claim_stale_received(older_than=datetime.now(UTC)) == []
    assert mailbox.list_events(message_id="message-1")[-1]["event_kind"] == "attention_emitted"


def test_stop_request_prevents_more_receipts(tmp_path: Path) -> None:
    _database, mailbox, conversations = _setup(tmp_path)
    recipient = conversations[0]
    mailbox.enqueue("message-1", [recipient])
    listener = mailbox.acquire_listener(recipient, listener_id="listener-a")

    stopped = mailbox.request_listener_stop(recipient)

    assert stopped is not None and stopped["stop_requested_at"] is not None
    with pytest.raises(ValueError, match="stop"):
        mailbox.receive_pending(
            recipient,
            listener_id=listener["listener_id"],
            fencing_token=listener["fencing_token"],
        )
