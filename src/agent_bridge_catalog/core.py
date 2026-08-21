"""Stores for the conversation-centric Agent Bridge product surface."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import func, select

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
    NatsEventRow,
    RoomMemberRow,
    RoomRow,
)


class Publisher(Protocol):
    async def publish(self, envelope: BridgeEnvelope, *, subject: str | None = None) -> Any: ...


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


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
        if mode is not None and mode not in {"wake", "notify", "digest"}:
            raise ValueError("room delivery mode must be wake, notify, or digest")
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
        body: str,
        target_conversation_id: str | None,
        room_id: str | None,
        source_conversation_id: str | None,
        actor_kind: str,
        operation: str,
        correlation_id: str | None,
        causation_id: str | None,
        delivery_strategy: DeliveryStrategy = "queue",
    ) -> dict[str, Any]:
        if bool(target_conversation_id) == bool(room_id):
            raise ValueError("provide exactly one conversation or room target")
        if operation not in {"message", "request", "reply", "forward", "complete", "needs_user"}:
            raise ValueError("unsupported message operation")
        if delivery_strategy not in {"queue", "steer-or-queue"}:
            raise ValueError("unsupported delivery strategy")
        now = datetime.now(UTC)
        message_id = f"message-{uuid4().hex}"
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
            body={"text": body, "operation": operation, "actor_kind": actor_kind},
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
            "delivery_route": row.delivery_route,
            "state": row.state,
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
