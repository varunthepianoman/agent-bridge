"""Catalog-independent observation hook for broker activity projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol


class BrokerActivityKind(StrEnum):
    PUBLISHED = "published"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    RETRY_SCHEDULED = "retry_scheduled"
    LEASE_EXTENDED = "lease_extended"
    DEAD_LETTERED = "dead_lettered"


@dataclass(frozen=True)
class BrokerActivity:
    kind: BrokerActivityKind
    subject: str
    message_id: str | None = None
    correlation_id: str | None = None
    stream: str | None = None
    stream_sequence: int | None = None
    consumer: str | None = None
    consumer_sequence: int | None = None
    delivery_count: int | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    detail: dict[str, Any] = field(default_factory=dict)


class TransportObserver(Protocol):
    """Best-effort activity sink; JetStream remains delivery authority."""

    async def record(self, activity: BrokerActivity) -> None: ...
