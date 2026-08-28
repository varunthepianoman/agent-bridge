"""Stores for the conversation-centric Agent Bridge product surface."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from agent_bridge_bridge.subjects import subject_for
from agent_bridge_protocol.models import (
    BridgeEnvelope,
    DeliveryPolicy,
    DeliveryStrategy,
    EndpointKind,
    EndpointRef,
    MessageKind,
)

from .db import (
    AttentionRow,
    CollectionMemberRow,
    CollectionRow,
    ConversationMessageRow,
    ConversationRow,
    Database,
    MailboxDeliveryRow,
    MailboxEventRow,
    MailboxListenerRow,
    NatsEventRow,
    RoomMemberRow,
    RoomRow,
)


class Publisher(Protocol):
    async def publish(self, envelope: BridgeEnvelope, *, subject: str | None = None) -> Any: ...


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


class CollectionStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(select(CollectionRow).order_by(CollectionRow.name)).all()
            return [self._dict(session, row) for row in rows]

    def create(
        self, *, name: str, description: str, kind: str, filters: dict[str, Any]
    ) -> dict[str, Any]:
        if kind not in {"manual", "smart"}:
            raise ValueError("collection kind must be manual or smart")
        now = datetime.now(UTC)
        row = CollectionRow(
            collection_id=f"collection-{uuid4().hex}",
            name=name,
            description=description,
            kind=kind,
            filter_json=json.dumps(filters, separators=(",", ":")),
            created_at=now,
            updated_at=now,
        )
        with self.database.session() as session:
            session.add(row)
            session.commit()
            return self._dict(session, row)

    def update(self, collection_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(CollectionRow, collection_id)
            if row is None:
                return None
            if "name" in changes:
                row.name = str(changes["name"])
            if "description" in changes:
                row.description = str(changes["description"])
            if "filters" in changes:
                row.filter_json = json.dumps(changes["filters"], separators=(",", ":"))
            row.updated_at = datetime.now(UTC)
            session.commit()
            return self._dict(session, row)

    def delete(self, collection_id: str) -> bool:
        with self.database.session() as session:
            row = session.get(CollectionRow, collection_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def set_member(self, collection_id: str, conversation_id: str, *, present: bool) -> bool:
        with self.database.session() as session:
            collection = session.get(CollectionRow, collection_id)
            conversation = session.get(ConversationRow, conversation_id)
            if collection is None or conversation is None:
                return False
            row = session.get(CollectionMemberRow, (collection_id, conversation_id))
            if present and row is None:
                session.add(
                    CollectionMemberRow(
                        collection_id=collection_id,
                        conversation_id=conversation_id,
                        added_at=datetime.now(UTC),
                    )
                )
            elif not present and row is not None:
                session.delete(row)
            collection.updated_at = datetime.now(UTC)
            session.commit()
            return True

    @staticmethod
    def _dict(session: Any, row: CollectionRow) -> dict[str, Any]:
        members = session.scalars(
            select(CollectionMemberRow.conversation_id).where(
                CollectionMemberRow.collection_id == row.collection_id
            )
        ).all()
        return {
            "collection_id": row.collection_id,
            "name": row.name,
            "description": row.description,
            "kind": row.kind,
            "filters": json.loads(row.filter_json),
            "conversation_ids": list(members),
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }


class AttentionStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        category: str,
        kind: str,
        title: str,
        detail: str = "",
        conversation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        row = AttentionRow(
            attention_id=f"attention-{uuid4().hex}",
            category=category,
            kind=kind,
            title=title,
            detail=detail,
            conversation_id=conversation_id,
            correlation_id=correlation_id,
            acknowledged=False,
            created_at=datetime.now(UTC),
        )
        with self.database.session() as session:
            session.add(row)
            session.commit()
        return self._dict(row)

    def list(
        self, *, category: str | None = None, unread_only: bool = False
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            statement = select(AttentionRow)
            if category:
                statement = statement.where(AttentionRow.category == category)
            if unread_only:
                statement = statement.where(AttentionRow.acknowledged.is_(False))
            rows = session.scalars(statement.order_by(AttentionRow.created_at.desc())).all()
            return [self._dict(row) for row in rows]

    def acknowledge(self, attention_id: str) -> bool:
        with self.database.session() as session:
            row = session.get(AttentionRow, attention_id)
            if row is None:
                return False
            row.acknowledged = True
            row.acknowledged_at = datetime.now(UTC)
            session.commit()
            return True

    def acknowledge_all(self) -> int:
        count = 0
        with self.database.session() as session:
            rows = session.scalars(
                select(AttentionRow).where(AttentionRow.acknowledged.is_(False))
            ).all()
            now = datetime.now(UTC)
            for row in rows:
                row.acknowledged = True
                row.acknowledged_at = now
                count += 1
            session.commit()
        return count

    @staticmethod
    def _dict(row: AttentionRow) -> dict[str, Any]:
        return {
            "attention_id": row.attention_id,
            "conversation_id": row.conversation_id,
            "correlation_id": row.correlation_id,
            "category": row.category,
            "kind": row.kind,
            "title": row.title,
            "detail": row.detail,
            "acknowledged": row.acknowledged,
            "created_at": _iso(row.created_at),
            "acknowledged_at": _iso(row.acknowledged_at),
        }


class RoomStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(select(RoomRow).order_by(RoomRow.name)).all()
            return [self._dict(session, row) for row in rows]

    def create(self, *, name: str, description: str = "") -> dict[str, Any]:
        now = datetime.now(UTC)
        row = RoomRow(
            room_id=f"room-{uuid4().hex}",
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
        )
        with self.database.session() as session:
            session.add(row)
            session.commit()
            return self._dict(session, row)

    def update(self, room_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(RoomRow, room_id)
            if row is None:
                return None
            if "name" in changes:
                row.name = str(changes["name"])
            if "description" in changes:
                row.description = str(changes["description"])
            row.updated_at = datetime.now(UTC)
            session.commit()
            return self._dict(session, row)

    def delete(self, room_id: str) -> bool:
        with self.database.session() as session:
            row = session.get(RoomRow, room_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def set_member(self, room_id: str, conversation_id: str, *, mode: str | None) -> bool:
        if mode is not None and mode not in {"mailbox", "notify", "digest"}:
            raise ValueError("room delivery mode must be mailbox, notify, or digest")
        with self.database.session() as session:
            room = session.get(RoomRow, room_id)
            conversation = session.get(ConversationRow, conversation_id)
            if room is None or conversation is None:
                return False
            row = session.get(RoomMemberRow, (room_id, conversation_id))
            if mode is not None:
                if row is None:
                    session.add(
                        RoomMemberRow(
                            room_id=room_id,
                            conversation_id=conversation_id,
                            delivery_mode=mode,
                            added_at=datetime.now(UTC),
                        )
                    )
                else:
                    row.delivery_mode = mode
            elif row is not None:
                session.delete(row)
            room.updated_at = datetime.now(UTC)
            session.commit()
            return True

    @staticmethod
    def _dict(session: Any, row: RoomRow) -> dict[str, Any]:
        members = session.scalars(
            select(RoomMemberRow).where(RoomMemberRow.room_id == row.room_id)
        ).all()
        return {
            "room_id": row.room_id,
            "name": row.name,
            "description": row.description,
            "members": [
                {"conversation_id": member.conversation_id, "delivery_mode": member.delivery_mode}
                for member in members
            ],
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }


MailboxOutcome = Literal["succeeded", "blocked", "failed"]
_MAILBOX_TERMINAL_STATES = frozenset({"succeeded", "blocked", "failed"})


class MailboxStore:
    """Durable, per-recipient mailbox processing state.

    ``ConversationMessageRow.state`` remains the transport projection. This store owns only
    recipient processing state and its append-only transition history.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def enqueue(
        self, message_id: str, recipient_conversation_ids: Iterable[str]
    ) -> list[dict[str, Any]]:
        recipients = list(dict.fromkeys(recipient_conversation_ids))
        if not recipients:
            return []
        now = datetime.now(UTC)
        with self.database.session() as session:
            if session.get(ConversationMessageRow, message_id) is None:
                raise ValueError("unknown mailbox message")
            known = set(
                session.scalars(
                    select(ConversationRow.conversation_id).where(
                        ConversationRow.conversation_id.in_(recipients)
                    )
                ).all()
            )
            missing = sorted(set(recipients) - known)
            if missing:
                raise ValueError(f"unknown mailbox recipients: {', '.join(missing)}")
            for recipient in recipients:
                key = (message_id, recipient)
                if session.get(MailboxDeliveryRow, key) is not None:
                    continue
                row = MailboxDeliveryRow(
                    message_id=message_id,
                    recipient_conversation_id=recipient,
                    state="pending",
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                self._add_event(session, row, event_kind="created", to_state="pending", now=now)
            session.commit()
            return [self._delivery(session, message_id, recipient) for recipient in recipients]

    def list_inbox(
        self,
        conversation_id: str,
        *,
        state: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        self._validate_limit(limit)
        with self.database.session() as session:
            statement = (
                select(MailboxDeliveryRow, ConversationMessageRow)
                .join(
                    ConversationMessageRow,
                    ConversationMessageRow.message_id == MailboxDeliveryRow.message_id,
                )
                .where(MailboxDeliveryRow.recipient_conversation_id == conversation_id)
            )
            if state is not None:
                statement = statement.where(MailboxDeliveryRow.state == state)
            rows = session.execute(
                statement.order_by(
                    ConversationMessageRow.created_at, ConversationMessageRow.message_id
                ).limit(limit)
            ).all()
            return [self._delivery_dict(delivery, message) for delivery, message in rows]

    def get_delivery(
        self, message_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        with self.database.session() as session:
            delivery = session.get(MailboxDeliveryRow, (message_id, conversation_id))
            if delivery is None:
                return None
            return self._delivery(session, message_id, conversation_id)

    def list_message_deliveries(
        self, message_id: str, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        self._validate_limit(limit)
        with self.database.session() as session:
            recipients = session.scalars(
                select(MailboxDeliveryRow.recipient_conversation_id)
                .where(MailboxDeliveryRow.message_id == message_id)
                .order_by(MailboxDeliveryRow.recipient_conversation_id)
                .limit(limit)
            ).all()
            return [self._delivery(session, message_id, recipient) for recipient in recipients]

    def receive_pending(
        self,
        conversation_id: str,
        *,
        listener_id: str,
        fencing_token: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self._validate_limit(limit)
        now = datetime.now(UTC)
        claimed: list[str] = []
        with self.database.session() as session:
            self._require_live_listener(
                session, conversation_id, listener_id, fencing_token, now=now
            )
            candidates = session.scalars(
                select(MailboxDeliveryRow)
                .join(
                    ConversationMessageRow,
                    ConversationMessageRow.message_id == MailboxDeliveryRow.message_id,
                )
                .where(
                    MailboxDeliveryRow.recipient_conversation_id == conversation_id,
                    MailboxDeliveryRow.state == "pending",
                )
                .order_by(ConversationMessageRow.created_at, ConversationMessageRow.message_id)
                .limit(limit)
            ).all()
            for candidate in candidates:
                result = session.execute(
                    update(MailboxDeliveryRow)
                    .where(
                        MailboxDeliveryRow.message_id == candidate.message_id,
                        MailboxDeliveryRow.recipient_conversation_id == conversation_id,
                        MailboxDeliveryRow.state == "pending",
                    )
                    .values(
                        state="received",
                        listener_id=listener_id,
                        fencing_token=fencing_token,
                        received_at=now,
                        updated_at=now,
                        revision=MailboxDeliveryRow.revision + 1,
                    ),
                    execution_options={"synchronize_session": False},
                )
                if cast(Any, result).rowcount != 1:
                    continue
                candidate.state = "received"
                candidate.listener_id = listener_id
                candidate.fencing_token = fencing_token
                candidate.received_at = now
                candidate.updated_at = now
                candidate.revision += 1
                self._add_event(
                    session,
                    candidate,
                    event_kind="received",
                    from_state="pending",
                    to_state="received",
                    listener_id=listener_id,
                    fencing_token=fencing_token,
                    now=now,
                )
                claimed.append(candidate.message_id)
            session.commit()
            return [self._delivery(session, message_id, conversation_id) for message_id in claimed]

    def acknowledge(
        self,
        message_id: str,
        conversation_id: str,
        *,
        detail: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.database.session() as session:
            row = self._require_delivery(session, message_id, conversation_id)
            message = session.get(ConversationMessageRow, message_id)
            assert message is not None
            if not message.acknowledgement_requested:
                raise ValueError("mailbox acknowledgement was not requested")
            if row.acknowledged_at is not None:
                return self._delivery(session, message_id, conversation_id)
            if row.state != "received":
                raise ValueError("only a claimed mailbox delivery can be acknowledged")

            result = session.execute(
                update(MailboxDeliveryRow)
                .where(
                    MailboxDeliveryRow.message_id == message_id,
                    MailboxDeliveryRow.recipient_conversation_id == conversation_id,
                    MailboxDeliveryRow.state == "received",
                    MailboxDeliveryRow.acknowledged_at.is_(None),
                )
                .values(
                    acknowledged_at=now,
                    acknowledgement_detail=detail,
                    acknowledgement_attention_emitted_at=now,
                    updated_at=now,
                    revision=MailboxDeliveryRow.revision + 1,
                )
            )
            if cast(Any, result).rowcount != 1:
                session.rollback()
                current = self._require_delivery(session, message_id, conversation_id)
                if current.acknowledged_at is not None:
                    return self._delivery(session, message_id, conversation_id)
                raise ValueError("mailbox delivery could not be acknowledged")
            session.expire_all()
            row = self._require_delivery(session, message_id, conversation_id)
            self._add_event(
                session,
                row,
                event_kind="acknowledged",
                from_state="received",
                to_state="received",
                listener_id=row.listener_id,
                fencing_token=row.fencing_token,
                detail=detail,
                now=now,
            )
            self._add_receipt_attention(
                session,
                message,
                row,
                kind="mailbox_acknowledged",
                title="Mailbox message acknowledged",
                detail=detail or "The recipient acknowledged the message.",
                now=now,
            )
            session.commit()
            return self._delivery(session, message_id, conversation_id)

    def complete(
        self,
        message_id: str,
        conversation_id: str,
        *,
        outcome: MailboxOutcome,
        detail: str | None,
        listener_id: str,
        fencing_token: int,
        reply_message_id: str | None = None,
    ) -> dict[str, Any]:
        if outcome not in _MAILBOX_TERMINAL_STATES:
            raise ValueError("mailbox outcome must be succeeded, blocked, or failed")
        now = datetime.now(UTC)
        with self.database.session() as session:
            row = self._require_delivery(session, message_id, conversation_id)
            self._require_claim(row, listener_id, fencing_token)
            if row.state in _MAILBOX_TERMINAL_STATES:
                if row.state != outcome or row.reply_message_id != reply_message_id:
                    raise ValueError("mailbox delivery already has a conflicting outcome")
                return self._delivery(session, message_id, conversation_id)
            if row.state != "received":
                raise ValueError("only a received mailbox delivery can be completed")
            # Completion validates the durable claim, not the live lease: the listener wait may
            # legitimately have ended while the agent processes the received batch.
            message = session.get(ConversationMessageRow, message_id)
            assert message is not None
            result = session.execute(
                update(MailboxDeliveryRow)
                .where(
                    MailboxDeliveryRow.message_id == message_id,
                    MailboxDeliveryRow.recipient_conversation_id == conversation_id,
                    MailboxDeliveryRow.state == "received",
                    MailboxDeliveryRow.listener_id == listener_id,
                    MailboxDeliveryRow.fencing_token == fencing_token,
                )
                .values(
                    state=outcome,
                    detail=detail,
                    reply_message_id=reply_message_id,
                    acknowledged_at=row.acknowledged_at or now,
                    completed_at=now,
                    terminal_attention_emitted_at=(
                        now
                        if message.acknowledgement_requested
                        else row.terminal_attention_emitted_at
                    ),
                    updated_at=now,
                    revision=MailboxDeliveryRow.revision + 1,
                ),
                execution_options={"synchronize_session": False},
            )
            if cast(Any, result).rowcount != 1:
                session.rollback()
                current = self._require_delivery(session, message_id, conversation_id)
                self._require_claim(current, listener_id, fencing_token)
                if current.state in _MAILBOX_TERMINAL_STATES:
                    if current.state != outcome or current.reply_message_id != reply_message_id:
                        raise ValueError("mailbox delivery already has a conflicting outcome")
                    return self._delivery(session, message_id, conversation_id)
                raise ValueError("mailbox delivery could not be completed")
            session.expire_all()
            row = self._require_delivery(session, message_id, conversation_id)
            self._add_event(
                session,
                row,
                event_kind="completed",
                from_state="received",
                to_state=outcome,
                listener_id=listener_id,
                fencing_token=fencing_token,
                detail=detail,
                now=now,
            )
            if message.acknowledgement_requested:
                self._add_receipt_attention(
                    session,
                    message,
                    row,
                    kind="mailbox_terminal",
                    title=f"Mailbox message {outcome}",
                    detail=detail or f"The recipient reported outcome: {outcome}.",
                    now=now,
                )
            session.commit()
            return self._delivery(session, message_id, conversation_id)

    def requeue(
        self, message_id: str, conversation_id: str, *, detail: str | None = None
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.database.session() as session:
            row = self._require_delivery(session, message_id, conversation_id)
            if row.state == "pending":
                return self._delivery(session, message_id, conversation_id)
            previous = row.state
            row.state = "pending"
            row.listener_id = None
            row.fencing_token = None
            row.detail = detail
            row.reply_message_id = None
            row.received_at = None
            row.acknowledged_at = None
            row.acknowledgement_detail = None
            row.completed_at = None
            row.attention_emitted_at = None
            row.acknowledgement_attention_emitted_at = None
            row.terminal_attention_emitted_at = None
            row.attempt += 1
            row.revision += 1
            row.updated_at = now
            self._add_event(
                session,
                row,
                event_kind="requeued",
                from_state=previous,
                to_state="pending",
                detail=detail,
                now=now,
            )
            session.commit()
            return self._delivery(session, message_id, conversation_id)

    def claim_stale_received(
        self, *, older_than: datetime, limit: int = 200
    ) -> list[dict[str, Any]]:
        self._validate_limit(limit)
        now = datetime.now(UTC)
        claimed: list[tuple[str, str]] = []
        with self.database.session() as session:
            candidates = session.scalars(
                select(MailboxDeliveryRow)
                .where(
                    MailboxDeliveryRow.state == "received",
                    MailboxDeliveryRow.received_at <= older_than,
                    MailboxDeliveryRow.attention_emitted_at.is_(None),
                )
                .order_by(MailboxDeliveryRow.received_at, MailboxDeliveryRow.message_id)
                .limit(limit)
            ).all()
            for candidate in candidates:
                result = session.execute(
                    update(MailboxDeliveryRow)
                    .where(
                        MailboxDeliveryRow.message_id == candidate.message_id,
                        MailboxDeliveryRow.recipient_conversation_id
                        == candidate.recipient_conversation_id,
                        MailboxDeliveryRow.state == "received",
                        MailboxDeliveryRow.attention_emitted_at.is_(None),
                    )
                    .values(attention_emitted_at=now, updated_at=now)
                )
                if cast(Any, result).rowcount != 1:
                    continue
                candidate.attention_emitted_at = now
                self._add_event(
                    session,
                    candidate,
                    event_kind="attention_emitted",
                    from_state="received",
                    to_state="received",
                    now=now,
                )
                claimed.append((candidate.message_id, candidate.recipient_conversation_id))
            session.commit()
            return [
                self._delivery(session, message_id, recipient)
                for message_id, recipient in claimed
            ]

    def acquire_listener(
        self,
        conversation_id: str,
        *,
        listener_id: str | None = None,
        lease_seconds: float = 60.0,
    ) -> dict[str, Any]:
        self._validate_lease(lease_seconds)
        resolved_id = listener_id or f"listener-{uuid4().hex}"
        for _attempt in range(3):
            now = datetime.now(UTC)
            expires_at = now + timedelta(seconds=lease_seconds)
            with self.database.session() as session:
                row = session.get(MailboxListenerRow, conversation_id)
                if row is None:
                    row = MailboxListenerRow(
                        conversation_id=conversation_id,
                        listener_id=resolved_id,
                        fencing_token=1,
                        started_at=now,
                        heartbeat_at=now,
                        expires_at=expires_at,
                    )
                    session.add(row)
                    try:
                        session.commit()
                    except IntegrityError:
                        session.rollback()
                        continue
                    return self._listener_dict(row)
                if not self._is_expired(row.expires_at, now):
                    raise ValueError("conversation already has an active mailbox listener")
                previous_token = row.fencing_token
                result = session.execute(
                    update(MailboxListenerRow)
                    .where(
                        MailboxListenerRow.conversation_id == conversation_id,
                        MailboxListenerRow.fencing_token == previous_token,
                        MailboxListenerRow.expires_at <= now,
                    )
                    .values(
                        listener_id=resolved_id,
                        fencing_token=previous_token + 1,
                        started_at=now,
                        heartbeat_at=now,
                        expires_at=expires_at,
                        stop_requested_at=None,
                    ),
                    execution_options={"synchronize_session": False},
                )
                if cast(Any, result).rowcount != 1:
                    session.rollback()
                    continue
                session.commit()
                session.expire_all()
                current = session.get(MailboxListenerRow, conversation_id)
                assert current is not None
                return self._listener_dict(current)
        raise ValueError("mailbox listener was concurrently acquired")

    def heartbeat_listener(
        self,
        conversation_id: str,
        *,
        listener_id: str,
        fencing_token: int,
        lease_seconds: float = 60.0,
    ) -> dict[str, Any]:
        self._validate_lease(lease_seconds)
        now = datetime.now(UTC)
        with self.database.session() as session:
            row = self._require_live_listener(
                session, conversation_id, listener_id, fencing_token, now=now
            )
            row.heartbeat_at = now
            row.expires_at = now + timedelta(seconds=lease_seconds)
            session.commit()
            return self._listener_dict(row)

    def request_listener_stop(self, conversation_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(MailboxListenerRow, conversation_id)
            if row is None or self._is_expired(row.expires_at, datetime.now(UTC)):
                return None
            if row.stop_requested_at is None:
                row.stop_requested_at = datetime.now(UTC)
                session.commit()
            return self._listener_dict(row)

    def release_listener(
        self, conversation_id: str, *, listener_id: str, fencing_token: int
    ) -> bool:
        now = datetime.now(UTC)
        with self.database.session() as session:
            row = session.get(MailboxListenerRow, conversation_id)
            if row is None:
                return False
            self._require_listener_identity(row, listener_id, fencing_token)
            if not self._is_expired(row.expires_at, now):
                row.expires_at = now
                row.heartbeat_at = now
                session.commit()
            return True

    def get_listener(self, conversation_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(MailboxListenerRow, conversation_id)
            if row is None or self._is_expired(row.expires_at, datetime.now(UTC)):
                return None
            return self._listener_dict(row)

    def list_events(
        self,
        *,
        message_id: str | None = None,
        conversation_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        self._validate_limit(limit)
        with self.database.session() as session:
            statement = select(MailboxEventRow)
            if message_id is not None:
                statement = statement.where(MailboxEventRow.message_id == message_id)
            if conversation_id is not None:
                statement = statement.where(
                    MailboxEventRow.recipient_conversation_id == conversation_id
                )
            rows = session.scalars(
                statement.order_by(MailboxEventRow.created_at, MailboxEventRow.event_id).limit(
                    limit
                )
            ).all()
            return [self._event_dict(row) for row in rows]

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if limit < 1 or limit > 1000:
            raise ValueError("mailbox limit must be between 1 and 1000")

    @staticmethod
    def _validate_lease(lease_seconds: float) -> None:
        if lease_seconds <= 0 or lease_seconds > 3600:
            raise ValueError("mailbox listener lease must be between 0 and 3600 seconds")

    @staticmethod
    def _is_expired(expires_at: datetime, now: datetime) -> bool:
        if expires_at.tzinfo is None:
            now = now.replace(tzinfo=None)
        return expires_at <= now

    def _require_live_listener(
        self,
        session: Any,
        conversation_id: str,
        listener_id: str,
        fencing_token: int,
        *,
        now: datetime,
    ) -> MailboxListenerRow:
        row = session.get(MailboxListenerRow, conversation_id)
        if row is None:
            raise ValueError("mailbox listener does not exist")
        self._require_listener_identity(row, listener_id, fencing_token)
        if self._is_expired(row.expires_at, now):
            raise ValueError("mailbox listener lease expired")
        if row.stop_requested_at is not None:
            raise ValueError("mailbox listener stop was requested")
        return cast(MailboxListenerRow, row)

    @staticmethod
    def _require_listener_identity(
        row: MailboxListenerRow, listener_id: str, fencing_token: int
    ) -> None:
        if row.listener_id != listener_id or row.fencing_token != fencing_token:
            raise ValueError("stale mailbox listener fencing token")

    @staticmethod
    def _require_claim(
        row: MailboxDeliveryRow, listener_id: str, fencing_token: int
    ) -> None:
        if row.listener_id != listener_id or row.fencing_token != fencing_token:
            raise ValueError("mailbox delivery belongs to a different listener claim")

    @staticmethod
    def _require_delivery(
        session: Any, message_id: str, conversation_id: str
    ) -> MailboxDeliveryRow:
        row = session.get(MailboxDeliveryRow, (message_id, conversation_id))
        if row is None:
            raise ValueError("mailbox delivery does not exist")
        return cast(MailboxDeliveryRow, row)

    def _delivery(self, session: Any, message_id: str, conversation_id: str) -> dict[str, Any]:
        row = session.execute(
            select(MailboxDeliveryRow, ConversationMessageRow)
            .join(
                ConversationMessageRow,
                ConversationMessageRow.message_id == MailboxDeliveryRow.message_id,
            )
            .where(
                MailboxDeliveryRow.message_id == message_id,
                MailboxDeliveryRow.recipient_conversation_id == conversation_id,
            )
        ).one()
        return self._delivery_dict(*row)

    @staticmethod
    def _delivery_dict(
        delivery: MailboxDeliveryRow, message: ConversationMessageRow
    ) -> dict[str, Any]:
        return {
            "message_id": delivery.message_id,
            "recipient_conversation_id": delivery.recipient_conversation_id,
            "state": delivery.state,
            "processing_state": "claimed" if delivery.state == "received" else delivery.state,
            "listener_id": delivery.listener_id,
            "fencing_token": delivery.fencing_token,
            "detail": delivery.detail,
            "processing_detail": delivery.detail,
            "outcome": delivery.state if delivery.state in _MAILBOX_TERMINAL_STATES else None,
            "outcome_detail": delivery.detail,
            "reply_message_id": delivery.reply_message_id,
            "created_at": _iso(message.created_at),
            "delivery_created_at": _iso(delivery.created_at),
            "updated_at": _iso(delivery.updated_at),
            "received_at": _iso(delivery.received_at),
            "claimed_at": _iso(delivery.received_at),
            "acknowledged_at": _iso(delivery.acknowledged_at),
            "acknowledgement_detail": delivery.acknowledgement_detail,
            "attempt": delivery.attempt,
            "revision": delivery.revision,
            "completed_at": _iso(delivery.completed_at),
            "outcome_at": _iso(delivery.completed_at),
            "attention_emitted_at": _iso(delivery.attention_emitted_at),
            "acknowledgement_attention_emitted_at": _iso(
                delivery.acknowledgement_attention_emitted_at
            ),
            "terminal_attention_emitted_at": _iso(delivery.terminal_attention_emitted_at),
            "correlation_id": message.correlation_id,
            "causation_id": message.causation_id,
            "source_conversation_id": message.source_conversation_id,
            "room_id": message.room_id,
            "actor_kind": message.actor_kind,
            "operation": message.operation,
            "body": message.body,
            "delivery_strategy": message.delivery_strategy,
            "acknowledgement_requested": message.acknowledgement_requested,
            "message_created_at": _iso(message.created_at),
        }

    @staticmethod
    def _add_receipt_attention(
        session: Any,
        message: ConversationMessageRow,
        delivery: MailboxDeliveryRow,
        *,
        kind: str,
        title: str,
        detail: str,
        now: datetime,
    ) -> None:
        source = message.source_conversation_id
        if source is None:
            raise ValueError("receipt message has no source conversation")
        identity = hashlib.sha256(
            (
                f"{message.message_id}\0{delivery.recipient_conversation_id}\0"
                f"{delivery.attempt}\0{kind}"
            ).encode()
        ).hexdigest()[:40]
        session.add(
            AttentionRow(
                attention_id=f"attention-receipt-{identity}",
                conversation_id=source,
                correlation_id=message.correlation_id,
                category="update",
                kind=kind,
                title=title,
                detail=detail,
                acknowledged=False,
                created_at=now,
            )
        )

    @staticmethod
    def _listener_dict(row: MailboxListenerRow) -> dict[str, Any]:
        return {
            "conversation_id": row.conversation_id,
            "listener_id": row.listener_id,
            "fencing_token": row.fencing_token,
            "started_at": _iso(row.started_at),
            "heartbeat_at": _iso(row.heartbeat_at),
            "expires_at": _iso(row.expires_at),
            "stop_requested_at": _iso(row.stop_requested_at),
            "stop_requested": row.stop_requested_at is not None,
            "state": "stopping" if row.stop_requested_at is not None else "waiting",
        }

    @staticmethod
    def _event_dict(row: MailboxEventRow) -> dict[str, Any]:
        return {
            "event_id": row.event_id,
            "message_id": row.message_id,
            "recipient_conversation_id": row.recipient_conversation_id,
            "event_kind": row.event_kind,
            "from_state": row.from_state,
            "to_state": row.to_state,
            "listener_id": row.listener_id,
            "fencing_token": row.fencing_token,
            "detail": row.detail,
            "created_at": _iso(row.created_at),
        }

    @staticmethod
    def _add_event(
        session: Any,
        delivery: MailboxDeliveryRow,
        *,
        event_kind: str,
        to_state: str,
        now: datetime,
        from_state: str | None = None,
        listener_id: str | None = None,
        fencing_token: int | None = None,
        detail: str | None = None,
    ) -> None:
        session.add(
            MailboxEventRow(
                event_id=f"mailbox-event-{uuid4().hex}",
                message_id=delivery.message_id,
                recipient_conversation_id=delivery.recipient_conversation_id,
                event_kind=event_kind,
                from_state=from_state,
                to_state=to_state,
                listener_id=listener_id,
                fencing_token=fencing_token,
                detail=detail,
                created_at=now,
            )
        )


class MessageStore:
    def __init__(
        self, database: Database, publisher: Publisher | None, attention: AttentionStore
    ) -> None:
        self.database = database
        self.publisher = publisher
        self.attention = attention

    async def send(
        self,
        *,
        message_id: str | None = None,
        body: str,
        target_conversation_id: str | None,
        room_id: str | None,
        source_conversation_id: str | None,
        actor_kind: str,
        operation: str,
        correlation_id: str | None,
        causation_id: str | None,
        acknowledgement_requested: bool = False,
        delivery_strategy: DeliveryStrategy = "mailbox",
    ) -> dict[str, Any]:
        if bool(target_conversation_id) == bool(room_id):
            raise ValueError("provide exactly one conversation or room target")
        if operation not in {"message", "request", "reply", "forward", "complete", "needs_user"}:
            raise ValueError("unsupported message operation")
        if delivery_strategy not in {"mailbox", "queue", "steer-or-queue"}:
            raise ValueError("unsupported delivery strategy")
        if acknowledgement_requested:
            if room_id is not None or target_conversation_id is None:
                raise ValueError("acknowledgement receipts require a direct conversation target")
            if delivery_strategy != "mailbox":
                raise ValueError("acknowledgement receipts require mailbox delivery")
            if source_conversation_id is None:
                raise ValueError("acknowledgement receipts require a source conversation")
            with self.database.session() as session:
                if session.get(ConversationRow, source_conversation_id) is None:
                    raise ValueError("source conversation not found")
        if message_id:
            existing = self.get(message_id)
            if existing is not None:
                expected = {
                    "body": body,
                    "target_conversation_id": target_conversation_id,
                    "room_id": room_id,
                    "source_conversation_id": source_conversation_id,
                    "actor_kind": actor_kind,
                    "operation": operation,
                    "correlation_id": correlation_id,
                    "causation_id": causation_id,
                    "acknowledgement_requested": acknowledgement_requested,
                }
                if any(existing[key] != value for key, value in expected.items()):
                    raise ValueError("message id already exists with different content")
                return existing
        now = datetime.now(UTC)
        message_id = message_id or f"message-{uuid4().hex}"
        correlation = correlation_id or f"correlation-{uuid4().hex}"
        target_kind = EndpointKind.CONVERSATION if target_conversation_id else EndpointKind.ROOM
        target_id = target_conversation_id or room_id or ""
        sender = EndpointRef(
            kind=EndpointKind.CONVERSATION if source_conversation_id else EndpointKind.ENDPOINT,
            id=source_conversation_id or "human",
        )
        envelope = BridgeEnvelope(
            message_id=message_id,
            kind=MessageKind.REQUEST if operation == "request" else MessageKind.MESSAGE,
            sender=sender,
            destination=EndpointRef(kind=target_kind, id=target_id),
            body={
                "text": body,
                "operation": operation,
                "actor_kind": actor_kind,
                "acknowledgement_requested": acknowledgement_requested,
            },
            correlation_id=correlation,
            causation_id=causation_id,
            delivery=DeliveryPolicy(strategy=delivery_strategy),
        )
        row = ConversationMessageRow(
            message_id=message_id,
            correlation_id=correlation,
            causation_id=causation_id,
            source_conversation_id=source_conversation_id,
            target_conversation_id=target_conversation_id,
            room_id=room_id,
            actor_kind=actor_kind,
            operation=operation,
            body=body,
            delivery_strategy=delivery_strategy,
            acknowledgement_requested=acknowledgement_requested,
            state="queued",
            created_at=now,
            updated_at=now,
        )
        with self.database.session() as session:
            session.add(row)
            session.commit()
        if self.publisher is None:
            row.state = "pending_broker"
        else:
            try:
                ack = await self.publisher.publish(envelope)
                row.state = "published"
                row.subject = subject_for(envelope)
                del ack
            except Exception as exc:
                row.state = "failed"
                row.error = str(exc)
                self.attention.create(
                    category="needs_attention",
                    kind="delivery_failed",
                    title="Bridge message delivery failed",
                    detail=str(exc),
                    conversation_id=target_conversation_id,
                    correlation_id=correlation,
                )
        row.updated_at = datetime.now(UTC)
        with self.database.session() as session:
            stored = session.get(ConversationMessageRow, message_id)
            assert stored is not None
            stored.state = row.state
            stored.subject = row.subject
            stored.error = row.error
            stored.updated_at = row.updated_at
            session.commit()
        if operation == "needs_user":
            self.attention.create(
                category="needs_attention",
                kind="needs_user",
                title="Agent requested attention",
                detail=body,
                conversation_id=source_conversation_id,
                correlation_id=correlation,
            )
        return self.get(message_id) or {}

    def list(self, *, correlation_id: str | None = None) -> list[dict[str, Any]]:
        with self.database.session() as session:
            statement = select(ConversationMessageRow)
            if correlation_id:
                statement = statement.where(ConversationMessageRow.correlation_id == correlation_id)
            rows = session.scalars(
                statement.order_by(ConversationMessageRow.created_at.desc())
            ).all()
            return [self._dict(row) for row in rows]

    def record_incoming(
        self,
        envelope: BridgeEnvelope,
        *,
        target_conversation_id: str | None,
        room_id: str | None,
        subject: str,
    ) -> dict[str, Any]:
        existing = self.get(envelope.message_id)
        if existing is not None:
            return existing
        body = envelope.body.get("text", "")
        operation = envelope.body.get("operation", "message")
        actor_kind = envelope.body.get("actor_kind", "agent")
        acknowledgement_requested = envelope.body.get("acknowledgement_requested", False)
        if not isinstance(acknowledgement_requested, bool):
            raise ValueError("acknowledgement_requested must be a boolean")
        if acknowledgement_requested and (
            room_id is not None
            or target_conversation_id is None
            or envelope.sender.kind != EndpointKind.CONVERSATION
        ):
            raise ValueError("acknowledgement receipts require direct conversations")
        now = datetime.now(UTC)
        row = ConversationMessageRow(
            message_id=envelope.message_id,
            correlation_id=envelope.correlation_id or envelope.message_id,
            causation_id=envelope.causation_id,
            source_conversation_id=(
                envelope.sender.id if envelope.sender.kind == EndpointKind.CONVERSATION else None
            ),
            target_conversation_id=target_conversation_id,
            room_id=room_id,
            actor_kind=str(actor_kind),
            operation=str(operation),
            body=str(body),
            delivery_strategy=envelope.delivery.strategy,
            acknowledgement_requested=acknowledgement_requested,
            state="received",
            subject=subject,
            created_at=envelope.created_at,
            updated_at=now,
        )
        with self.database.session() as session:
            session.add(row)
            session.commit()
        return self._dict(row)

    def set_state(
        self,
        message_id: str,
        state: str,
        *,
        error: str | None = None,
        delivery_route: str | None = None,
    ) -> None:
        with self.database.session() as session:
            row = session.get(ConversationMessageRow, message_id)
            if row is None:
                return
            row.state = state
            row.error = error
            if delivery_route is not None:
                row.delivery_route = delivery_route
            row.updated_at = datetime.now(UTC)
            session.commit()

    def get(self, message_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(ConversationMessageRow, message_id)
            return self._dict(row) if row else None

    def correlation(self, correlation_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            rows = session.scalars(
                select(ConversationMessageRow)
                .where(ConversationMessageRow.correlation_id == correlation_id)
                .order_by(ConversationMessageRow.created_at)
            ).all()
            participants = {
                value
                for row in rows
                for value in (row.source_conversation_id, row.target_conversation_id)
                if value
            }
            return {
                "correlation_id": correlation_id,
                "message_count": len(rows),
                "participants": sorted(participants),
                "started_at": _iso(rows[0].created_at) if rows else None,
                "updated_at": _iso(rows[-1].updated_at) if rows else None,
                "messages": [self._dict(row) for row in rows],
            }

    @staticmethod
    def _dict(row: ConversationMessageRow) -> dict[str, Any]:
        return {
            "message_id": row.message_id,
            "correlation_id": row.correlation_id,
            "causation_id": row.causation_id,
            "source_conversation_id": row.source_conversation_id,
            "target_conversation_id": row.target_conversation_id,
            "room_id": row.room_id,
            "actor_kind": row.actor_kind,
            "operation": row.operation,
            "body": row.body,
            "delivery_strategy": row.delivery_strategy,
            "acknowledgement_requested": row.acknowledgement_requested,
            "delivery_route": row.delivery_route,
            "state": row.state,
            "transport_state": row.state,
            "subject": row.subject,
            "error": row.error,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }


class NatsEventStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def record(self, *, category: str, detail: dict[str, Any], **fields: Any) -> None:
        row = NatsEventRow(
            event_id=f"nats-{uuid4().hex}",
            category=category,
            direction=fields.get("direction"),
            severity=fields.get("severity", "info"),
            subject=fields.get("subject"),
            message_id=fields.get("message_id"),
            correlation_id=fields.get("correlation_id"),
            node_id=fields.get("node_id"),
            detail_json=json.dumps(detail, default=str, separators=(",", ":")),
            occurred_at=fields.get("occurred_at") or datetime.now(UTC),
        )
        with self.database.session() as session:
            session.add(row)
            session.commit()

    def list(self, *, category: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self.database.session() as session:
            statement = select(NatsEventRow)
            if category:
                statement = statement.where(NatsEventRow.category == category)
            rows = session.scalars(
                statement.order_by(NatsEventRow.occurred_at.desc()).limit(limit)
            ).all()
            return [
                {
                    "event_id": row.event_id,
                    "category": row.category,
                    "direction": row.direction,
                    "severity": row.severity,
                    "subject": row.subject,
                    "message_id": row.message_id,
                    "correlation_id": row.correlation_id,
                    "node_id": row.node_id,
                    "detail": json.loads(row.detail_json),
                    "occurred_at": _iso(row.occurred_at),
                }
                for row in rows
            ]

    def summary(self) -> dict[str, Any]:
        with self.database.session() as session:
            return {
                "events": int(session.scalar(select(func.count()).select_from(NatsEventRow)) or 0),
                "issues": int(
                    session.scalar(
                        select(func.count())
                        .select_from(NatsEventRow)
                        .where(NatsEventRow.severity.in_(("warning", "error")))
                    )
                    or 0
                ),
            }
