from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_bridge_catalog.core import AttentionStore, MailboxStore, MessageStore
from agent_bridge_catalog.db import (
    AttentionRow,
    ConversationMessageRow,
    Database,
    MailboxDeliveryRow,
)
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
    assert all(item["acknowledgement_requested"] is False for item in deliveries)
    assert all(item["attempt"] == 1 and item["revision"] == 0 for item in deliveries)
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
    assert received[0]["state"] == "received"
    assert received[0]["processing_state"] == "claimed"
    assert received[0]["claimed_at"] == received[0]["received_at"]
    assert received[0]["revision"] == 1
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
    assert mailbox.get_listener(recipient) is None
    assert mailbox.request_listener_stop(recipient) is None
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
    assert completed["acknowledged_at"] is not None
    assert completed["revision"] == 2
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


def _request_ack(database: Database) -> None:
    with database.session() as session:
        message = session.get(ConversationMessageRow, "message-1")
        assert message is not None
        message.acknowledgement_requested = True
        session.commit()


def _claim(
    mailbox: MailboxStore, recipient: str, listener_id: str = "listener-a"
) -> dict[str, Any]:
    listener = mailbox.acquire_listener(recipient, listener_id=listener_id)
    items = mailbox.receive_pending(
        recipient,
        listener_id=listener["listener_id"],
        fencing_token=listener["fencing_token"],
    )
    assert len(items) == 1
    return listener


def test_explicit_acknowledgement_is_requested_claimed_and_idempotent(tmp_path: Path) -> None:
    database, mailbox, conversations = _setup(tmp_path)
    recipient, wrong_recipient = conversations
    _request_ack(database)
    mailbox.enqueue("message-1", [recipient])

    with pytest.raises(ValueError, match="claimed"):
        mailbox.acknowledge("message-1", recipient, detail="too early")
    with pytest.raises(ValueError, match="does not exist"):
        mailbox.acknowledge("message-1", wrong_recipient)

    _claim(mailbox, recipient)
    acknowledged = mailbox.acknowledge("message-1", recipient, detail="working")

    assert acknowledged["state"] == "received"
    assert acknowledged["processing_state"] == "claimed"
    assert acknowledged["acknowledgement_detail"] == "working"
    assert acknowledged["acknowledged_at"] is not None
    assert acknowledged["revision"] == 2
    assert acknowledged["acknowledgement_attention_emitted_at"] is not None
    assert acknowledged["terminal_attention_emitted_at"] is None
    assert mailbox.acknowledge("message-1", recipient, detail="retry") == acknowledged
    assert [event["event_kind"] for event in mailbox.list_events(message_id="message-1")] == [
        "created",
        "received",
        "acknowledged",
    ]
    with database.session() as session:
        attention = session.query(AttentionRow).all()
        assert len(attention) == 1
        assert attention[0].kind == "mailbox_acknowledged"
        assert attention[0].conversation_id == conversations[1]


def test_unrequested_message_rejects_explicit_acknowledgement(tmp_path: Path) -> None:
    _database, mailbox, conversations = _setup(tmp_path)
    recipient = conversations[0]
    mailbox.enqueue("message-1", [recipient])
    _claim(mailbox, recipient)

    with pytest.raises(ValueError, match="was not requested"):
        mailbox.acknowledge("message-1", recipient)


def test_completion_implicitly_acknowledges_and_only_emits_terminal_attention(
    tmp_path: Path,
) -> None:
    database, mailbox, conversations = _setup(tmp_path)
    recipient = conversations[0]
    _request_ack(database)
    mailbox.enqueue("message-1", [recipient])
    listener = _claim(mailbox, recipient)

    completed = mailbox.complete(
        "message-1",
        recipient,
        outcome="succeeded",
        detail="done",
        listener_id=listener["listener_id"],
        fencing_token=listener["fencing_token"],
    )

    assert completed["acknowledged_at"] == completed["completed_at"]
    assert completed["acknowledgement_attention_emitted_at"] is None
    assert completed["terminal_attention_emitted_at"] is not None
    assert completed["revision"] == 2
    with database.session() as session:
        attention = session.query(AttentionRow).all()
        assert [item.kind for item in attention] == ["mailbox_terminal"]


def test_explicit_acknowledgement_then_completion_emits_each_receipt_once(
    tmp_path: Path,
) -> None:
    database, mailbox, conversations = _setup(tmp_path)
    recipient = conversations[0]
    _request_ack(database)
    mailbox.enqueue("message-1", [recipient])
    listener = _claim(mailbox, recipient)
    mailbox.acknowledge("message-1", recipient, detail="started")

    completed = mailbox.complete(
        "message-1",
        recipient,
        outcome="blocked",
        detail="need input",
        listener_id=listener["listener_id"],
        fencing_token=listener["fencing_token"],
    )
    retried = mailbox.complete(
        "message-1",
        recipient,
        outcome="blocked",
        detail="retry detail is ignored",
        listener_id=listener["listener_id"],
        fencing_token=listener["fencing_token"],
    )

    assert retried == completed
    assert completed["revision"] == 3
    with database.session() as session:
        attention = session.query(AttentionRow).order_by(AttentionRow.created_at).all()
        assert [item.kind for item in attention] == [
            "mailbox_acknowledged",
            "mailbox_terminal",
        ]


def test_requeue_starts_new_attempt_and_clears_current_receipt_state(tmp_path: Path) -> None:
    database, mailbox, conversations = _setup(tmp_path)
    recipient = conversations[0]
    _request_ack(database)
    mailbox.enqueue("message-1", [recipient])
    first_listener = _claim(mailbox, recipient)
    mailbox.acknowledge("message-1", recipient, detail="first attempt")

    requeued = mailbox.requeue("message-1", recipient, detail="retry")

    assert requeued["state"] == "pending"
    assert requeued["attempt"] == 2
    assert requeued["revision"] == 3
    assert requeued["claimed_at"] is None
    assert requeued["acknowledged_at"] is None
    assert requeued["acknowledgement_detail"] is None
    assert requeued["acknowledgement_attention_emitted_at"] is None
    assert requeued["terminal_attention_emitted_at"] is None

    mailbox.release_listener(
        recipient,
        listener_id=first_listener["listener_id"],
        fencing_token=first_listener["fencing_token"],
    )
    _claim(mailbox, recipient, listener_id="listener-b")
    second = mailbox.acknowledge("message-1", recipient, detail="second attempt")
    assert second["attempt"] == 2
    assert second["revision"] == 5
    with database.session() as session:
        attention = session.query(AttentionRow).all()
        assert len(attention) == 2
        assert len({item.attention_id for item in attention}) == 2


@pytest.mark.asyncio
async def test_message_store_validates_and_serializes_receipt_request(tmp_path: Path) -> None:
    database, _mailbox, conversations = _setup(tmp_path)
    messages = MessageStore(database, None, AttentionStore(database))

    sent = await messages.send(
        body="please inspect",
        target_conversation_id=conversations[0],
        room_id=None,
        source_conversation_id=conversations[1],
        actor_kind="agent",
        operation="request",
        correlation_id="receipt-correlation",
        causation_id=None,
        acknowledgement_requested=True,
    )
    assert sent["acknowledgement_requested"] is True

    with pytest.raises(ValueError, match="direct conversation"):
        await messages.send(
            body="room receipt",
            target_conversation_id=None,
            room_id="room-1",
            source_conversation_id=conversations[1],
            actor_kind="agent",
            operation="request",
            correlation_id=None,
            causation_id=None,
            acknowledgement_requested=True,
        )
    with pytest.raises(ValueError, match="source conversation not found"):
        await messages.send(
            body="unknown sender",
            target_conversation_id=conversations[0],
            room_id=None,
            source_conversation_id="missing",
            actor_kind="agent",
            operation="request",
            correlation_id=None,
            causation_id=None,
            acknowledgement_requested=True,
        )
