"""Provider-neutral active-turn message delivery contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ActiveTurnDeliveryState(StrEnum):
    DELIVERED = "delivered"
    UNAVAILABLE = "unavailable"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class ActiveTurnDeliveryResult:
    state: ActiveTurnDeliveryState
    detail: str | None = None


class ActiveTurnDelivery(Protocol):
    async def deliver(
        self,
        *,
        provider_thread_id: str,
        cwd: str,
        prompt: str,
        message_id: str,
    ) -> ActiveTurnDeliveryResult: ...
