"""Adapter from transport activity hooks to the SQL operational projection."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from agent_bridge_bridge.observer import BrokerActivity, BrokerActivityKind

from .broker_projection import BrokerProjectionStore


class BrokerProjectionObserver:
    """Best-effort materializer used by JetStreamTransport's observer hook."""

    def __init__(self, store: BrokerProjectionStore) -> None:
        self.store = store

    async def record(self, activity: BrokerActivity) -> None:
        await asyncio.to_thread(self._record, activity)

    def _record(self, activity: BrokerActivity) -> None:
        if activity.message_id is None:
            return
        detail = activity.detail
        self.store.materialize_message(
            message_id=activity.message_id,
            subject=activity.subject,
            message_type=_text(detail.get("message_type")),
            state=_message_state(activity.kind),
            observed_at=activity.occurred_at,
            stream=activity.stream,
            stream_sequence=activity.stream_sequence,
            source_kind=_text(detail.get("source_kind")),
            source_id=_text(detail.get("source_id")),
            destination_kind=_text(detail.get("destination_kind")),
            destination_id=_text(detail.get("destination_id")),
            correlation_id=activity.correlation_id,
            size_bytes=_integer(detail.get("encoded_size")),
            expires_at=_datetime(detail.get("expires_at")),
            payload_summary={"duplicate": bool(detail.get("duplicate", False))}
            if activity.kind == BrokerActivityKind.PUBLISHED
            else None,
        )
        if activity.kind == BrokerActivityKind.PUBLISHED or activity.consumer is None:
            return
        delivery_sequence = activity.consumer_sequence or activity.stream_sequence or 0
        delivery_id = (
            f"{activity.stream or 'unknown'}:{activity.consumer}:"
            f"{delivery_sequence}:{activity.delivery_count or 1}"
        )
        delivery_state = _delivery_state(activity.kind)
        self.store.materialize_delivery(
            delivery_id=delivery_id,
            message_id=activity.message_id,
            stream=activity.stream or "unknown",
            consumer=activity.consumer,
            delivery_sequence=delivery_sequence,
            state=delivery_state,
            delivered_at=activity.occurred_at,
            observed_at=activity.occurred_at,
            redelivery_count=max((activity.delivery_count or 1) - 1, 0),
            error=detail if delivery_state in {"retrying", "dead_lettered"} else None,
            acknowledged_at=(
                activity.occurred_at
                if activity.kind
                in {BrokerActivityKind.ACKNOWLEDGED, BrokerActivityKind.DEAD_LETTERED}
                else None
            ),
        )
        if activity.kind == BrokerActivityKind.DEAD_LETTERED:
            self.store.materialize_dead_letter(
                dead_letter_id=f"dlq:{activity.message_id}:{activity.consumer}",
                message_id=activity.message_id,
                stream="BRIDGE_DLQ_V1",
                consumer=activity.consumer,
                reason=_text(detail.get("reason")) or "delivery_failed",
                attempts=activity.delivery_count or 1,
                dead_lettered_at=activity.occurred_at,
                observed_at=activity.occurred_at,
                detail=detail,
            )


def _message_state(kind: BrokerActivityKind) -> str:
    return {
        BrokerActivityKind.PUBLISHED: "published",
        BrokerActivityKind.DELIVERED: "delivered",
        BrokerActivityKind.ACKNOWLEDGED: "acknowledged",
        BrokerActivityKind.RETRY_SCHEDULED: "retrying",
        BrokerActivityKind.LEASE_EXTENDED: "leased",
        BrokerActivityKind.DEAD_LETTERED: "dead_lettered",
    }[kind]


def _delivery_state(kind: BrokerActivityKind) -> str:
    return {
        BrokerActivityKind.DELIVERED: "delivered",
        BrokerActivityKind.ACKNOWLEDGED: "acknowledged",
        BrokerActivityKind.RETRY_SCHEDULED: "retrying",
        BrokerActivityKind.LEASE_EXTENDED: "leased",
        BrokerActivityKind.DEAD_LETTERED: "dead_lettered",
    }.get(kind, "unknown")


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
