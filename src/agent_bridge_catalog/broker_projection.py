from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from .db import (
    BrokerConsumerStateRow,
    BrokerDeadLetterRow,
    BrokerDeliveryRow,
    BrokerMessageRow,
    Database,
)


class BrokerProjectionStore:
    """Materialized operational view; JetStream remains the delivery authority."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def materialize_message(
        self,
        *,
        message_id: str,
        subject: str,
        message_type: str | None,
        state: str,
        observed_at: datetime | None = None,
        stream: str | None = None,
        stream_sequence: int | None = None,
        source_kind: str | None = None,
        source_id: str | None = None,
        destination_kind: str | None = None,
        destination_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        work_id: str | None = None,
        role_id: str | None = None,
        execution_id: str | None = None,
        size_bytes: int | None = None,
        payload_summary: dict[str, Any] | None = None,
        sent_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = _utc(observed_at or datetime.now(UTC))
        with self.database.session() as session:
            row = session.get(BrokerMessageRow, message_id)
            if row is None:
                row = BrokerMessageRow(
                    message_id=message_id,
                    subject=subject,
                    message_type=message_type or "unknown",
                    state=state,
                    first_observed_at=now,
                    last_observed_at=now,
                    payload_summary_json=_json(payload_summary or {}),
                )
                session.add(row)
            if now >= _utc(row.last_observed_at):
                row.subject = subject
                if message_type is not None:
                    row.message_type = message_type
                row.state = state
                row.stream = stream
                row.stream_sequence = stream_sequence
                row.source_kind = source_kind or row.source_kind
                row.source_id = source_id or row.source_id
                row.destination_kind = destination_kind or row.destination_kind
                row.destination_id = destination_id or row.destination_id
                row.correlation_id = correlation_id or row.correlation_id
                row.causation_id = causation_id or row.causation_id
                row.work_id = work_id or row.work_id
                row.role_id = role_id or row.role_id
                row.execution_id = execution_id or row.execution_id
                row.size_bytes = size_bytes if size_bytes is not None else row.size_bytes
                if payload_summary is not None:
                    row.payload_summary_json = _json(payload_summary)
                row.sent_at = _optional_utc(sent_at) or row.sent_at
                row.expires_at = _optional_utc(expires_at) or row.expires_at
                row.last_observed_at = now
            session.commit()
            return _message_dict(row)

    def materialize_delivery(
        self,
        *,
        delivery_id: str,
        message_id: str,
        stream: str,
        consumer: str,
        delivery_sequence: int,
        state: str,
        delivered_at: datetime,
        observed_at: datetime | None = None,
        redelivery_count: int = 0,
        node_id: str | None = None,
        error: dict[str, Any] | None = None,
        ack_deadline_at: datetime | None = None,
        acknowledged_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = _utc(observed_at or datetime.now(UTC))
        with self.database.session() as session:
            if session.get(BrokerMessageRow, message_id) is None:
                raise LookupError("broker message not found")
            row = session.get(BrokerDeliveryRow, delivery_id)
            if row is None:
                row = BrokerDeliveryRow(
                    delivery_id=delivery_id,
                    message_id=message_id,
                    stream=stream,
                    consumer=consumer,
                    delivery_sequence=delivery_sequence,
                    state=state,
                    delivered_at=_utc(delivered_at),
                    last_observed_at=now,
                )
                session.add(row)
            elif (
                row.message_id != message_id
                or row.consumer != consumer
                or row.delivery_sequence != delivery_sequence
            ):
                raise ValueError("delivery_id identifies a different delivery attempt")
            if now >= _utc(row.last_observed_at):
                row.stream = stream
                row.state = state
                row.redelivery_count = redelivery_count
                row.node_id = node_id
                row.error_json = _json(error) if error is not None else None
                row.delivered_at = _utc(delivered_at)
                row.ack_deadline_at = _optional_utc(ack_deadline_at)
                row.acknowledged_at = _optional_utc(acknowledged_at)
                row.last_observed_at = now
            session.commit()
            return _delivery_dict(row)

    def materialize_dead_letter(
        self,
        *,
        dead_letter_id: str,
        message_id: str,
        stream: str,
        consumer: str,
        reason: str,
        attempts: int,
        dead_lettered_at: datetime,
        observed_at: datetime | None = None,
        detail: dict[str, Any] | None = None,
        resolved_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = _utc(observed_at or datetime.now(UTC))
        with self.database.session() as session:
            if session.get(BrokerMessageRow, message_id) is None:
                raise LookupError("broker message not found")
            row = session.get(BrokerDeadLetterRow, dead_letter_id)
            if row is None:
                row = BrokerDeadLetterRow(
                    dead_letter_id=dead_letter_id,
                    message_id=message_id,
                    stream=stream,
                    consumer=consumer,
                    reason=reason,
                    attempts=attempts,
                    detail_json=_json(detail or {}),
                    dead_lettered_at=_utc(dead_lettered_at),
                    last_observed_at=now,
                )
                session.add(row)
            elif row.message_id != message_id or row.consumer != consumer:
                raise ValueError("dead_letter_id identifies a different dead letter")
            if now >= _utc(row.last_observed_at):
                row.stream = stream
                row.reason = reason
                row.attempts = attempts
                row.detail_json = _json(detail or {})
                row.dead_lettered_at = _utc(dead_lettered_at)
                row.resolved_at = _optional_utc(resolved_at)
                row.last_observed_at = now
            session.commit()
            return _dead_letter_dict(row)

    def materialize_consumer_state(
        self,
        *,
        stream: str,
        consumer: str,
        state: str,
        observed_at: datetime,
        pending_count: int = 0,
        ack_pending_count: int = 0,
        redelivered_count: int = 0,
        delivered_stream_sequence: int = 0,
        ack_floor_stream_sequence: int = 0,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = f"{stream}:{consumer}"
        observed = _utc(observed_at)
        with self.database.session() as session:
            row = session.get(BrokerConsumerStateRow, key)
            if row is None:
                row = BrokerConsumerStateRow(
                    consumer_key=key,
                    stream=stream,
                    consumer=consumer,
                    state=state,
                    observed_at=observed,
                )
                session.add(row)
            elif row.stream != stream or row.consumer != consumer:
                raise ValueError("consumer key collision")
            if observed >= _utc(row.observed_at):
                row.state = state
                row.pending_count = pending_count
                row.ack_pending_count = ack_pending_count
                row.redelivered_count = redelivered_count
                row.delivered_stream_sequence = delivered_stream_sequence
                row.ack_floor_stream_sequence = ack_floor_stream_sequence
                row.observed_at = observed
                row.detail_json = _json(detail or {})
            session.commit()
            return _consumer_dict(row)

    def list_messages(
        self,
        *,
        state: str | None = None,
        stream: str | None = None,
        correlation_id: str | None = None,
        work_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        filters = []
        if state:
            filters.append(BrokerMessageRow.state == state)
        if stream:
            filters.append(BrokerMessageRow.stream == stream)
        if correlation_id:
            filters.append(BrokerMessageRow.correlation_id == correlation_id)
        if work_id:
            filters.append(BrokerMessageRow.work_id == work_id)
        with self.database.session() as session:
            total = (
                session.scalar(select(func.count()).select_from(BrokerMessageRow).where(*filters))
                or 0
            )
            rows = session.scalars(
                select(BrokerMessageRow)
                .where(*filters)
                .order_by(BrokerMessageRow.last_observed_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [_message_dict(row) for row in rows], total

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(BrokerMessageRow, message_id)
            if row is None:
                return None
            result = _message_dict(row)
            deliveries = session.scalars(
                select(BrokerDeliveryRow)
                .where(BrokerDeliveryRow.message_id == message_id)
                .order_by(BrokerDeliveryRow.delivered_at)
            ).all()
            result["deliveries"] = [_delivery_dict(item) for item in deliveries]
            return result

    def list_deliveries(
        self,
        *,
        state: str | None = None,
        consumer: str | None = None,
        message_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        filters = []
        if state:
            filters.append(BrokerDeliveryRow.state == state)
        if consumer:
            filters.append(BrokerDeliveryRow.consumer == consumer)
        if message_id:
            filters.append(BrokerDeliveryRow.message_id == message_id)
        with self.database.session() as session:
            total = (
                session.scalar(select(func.count()).select_from(BrokerDeliveryRow).where(*filters))
                or 0
            )
            rows = session.scalars(
                select(BrokerDeliveryRow)
                .where(*filters)
                .order_by(BrokerDeliveryRow.delivered_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [_delivery_dict(row) for row in rows], total

    def list_dead_letters(
        self, *, unresolved_only: bool = True, limit: int = 100, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        filters = [BrokerDeadLetterRow.resolved_at.is_(None)] if unresolved_only else []
        with self.database.session() as session:
            total = (
                session.scalar(
                    select(func.count()).select_from(BrokerDeadLetterRow).where(*filters)
                )
                or 0
            )
            rows = session.scalars(
                select(BrokerDeadLetterRow)
                .where(*filters)
                .order_by(BrokerDeadLetterRow.dead_lettered_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [_dead_letter_dict(row) for row in rows], total

    def list_consumers(self, *, stream: str | None = None) -> list[dict[str, Any]]:
        statement = select(BrokerConsumerStateRow)
        if stream:
            statement = statement.where(BrokerConsumerStateRow.stream == stream)
        statement = statement.order_by(
            BrokerConsumerStateRow.stream, BrokerConsumerStateRow.consumer
        )
        with self.database.session() as session:
            return [_consumer_dict(row) for row in session.scalars(statement).all()]

    def summary(self) -> dict[str, Any]:
        with self.database.session() as session:

            def count(model: type[Any], *filters: Any) -> int:
                return int(
                    session.scalar(select(func.count()).select_from(model).where(*filters)) or 0
                )

            return {
                "messages": count(BrokerMessageRow),
                "deliveries": count(BrokerDeliveryRow),
                "pending_deliveries": count(
                    BrokerDeliveryRow, BrokerDeliveryRow.state.in_(["pending", "delivered"])
                ),
                "unresolved_dead_letters": count(
                    BrokerDeadLetterRow, BrokerDeadLetterRow.resolved_at.is_(None)
                ),
                "consumers": count(BrokerConsumerStateRow),
                "consumer_pending": int(
                    session.scalar(select(func.sum(BrokerConsumerStateRow.pending_count))) or 0
                ),
            }


def _message_dict(row: BrokerMessageRow) -> dict[str, Any]:
    return {
        "message_id": row.message_id,
        "subject": row.subject,
        "stream": row.stream,
        "stream_sequence": row.stream_sequence,
        "message_type": row.message_type,
        "source_kind": row.source_kind,
        "source_id": row.source_id,
        "destination_kind": row.destination_kind,
        "destination_id": row.destination_id,
        "correlation_id": row.correlation_id,
        "causation_id": row.causation_id,
        "work_id": row.work_id,
        "role_id": row.role_id,
        "execution_id": row.execution_id,
        "state": row.state,
        "size_bytes": row.size_bytes,
        "payload_summary": _object(row.payload_summary_json),
        "sent_at": _iso(row.sent_at),
        "expires_at": _iso(row.expires_at),
        "first_observed_at": _iso(row.first_observed_at),
        "last_observed_at": _iso(row.last_observed_at),
    }


def _delivery_dict(row: BrokerDeliveryRow) -> dict[str, Any]:
    return {
        "delivery_id": row.delivery_id,
        "message_id": row.message_id,
        "stream": row.stream,
        "consumer": row.consumer,
        "delivery_sequence": row.delivery_sequence,
        "redelivery_count": row.redelivery_count,
        "state": row.state,
        "node_id": row.node_id,
        "error": _object(row.error_json) if row.error_json else None,
        "delivered_at": _iso(row.delivered_at),
        "ack_deadline_at": _iso(row.ack_deadline_at),
        "acknowledged_at": _iso(row.acknowledged_at),
        "last_observed_at": _iso(row.last_observed_at),
    }


def _dead_letter_dict(row: BrokerDeadLetterRow) -> dict[str, Any]:
    return {
        "dead_letter_id": row.dead_letter_id,
        "message_id": row.message_id,
        "stream": row.stream,
        "consumer": row.consumer,
        "reason": row.reason,
        "attempts": row.attempts,
        "detail": _object(row.detail_json),
        "dead_lettered_at": _iso(row.dead_lettered_at),
        "last_observed_at": _iso(row.last_observed_at),
        "resolved_at": _iso(row.resolved_at),
    }


def _consumer_dict(row: BrokerConsumerStateRow) -> dict[str, Any]:
    observed_at = _utc(row.observed_at)
    return {
        "stream": row.stream,
        "consumer": row.consumer,
        "pending_count": row.pending_count,
        "ack_pending_count": row.ack_pending_count,
        "redelivered_count": row.redelivered_count,
        "delivered_stream_sequence": row.delivered_stream_sequence,
        "ack_floor_stream_sequence": row.ack_floor_stream_sequence,
        "state": row.state,
        "observed_at": _iso(observed_at),
        "stale": observed_at < datetime.now(UTC) - timedelta(minutes=2),
        "detail": _object(row.detail_json),
    }


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _object(value: str) -> dict[str, Any]:
    loaded = json.loads(value)
    return loaded if isinstance(loaded, dict) else {}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _utc(value) if value else None


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value else None
