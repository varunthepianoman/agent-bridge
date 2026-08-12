"""Idempotency claims used before externally visible runner side effects."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol


class ClaimResult(StrEnum):
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class IdempotencyStore(Protocol):
    async def claim(self, key: str, *, owner: str, ttl_seconds: float) -> ClaimResult: ...

    async def complete(self, key: str, *, owner: str) -> None: ...

    async def release(self, key: str, *, owner: str) -> None: ...


@dataclass
class _Claim:
    owner: str
    expires_at: datetime
    completed: bool = False


class InMemoryIdempotencyStore:
    """Process-local reference implementation; durable runners implement the protocol."""

    def __init__(self) -> None:
        self._claims: dict[str, _Claim] = {}
        self._lock = asyncio.Lock()

    async def claim(self, key: str, *, owner: str, ttl_seconds: float) -> ClaimResult:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        async with self._lock:
            now = datetime.now(UTC)
            existing = self._claims.get(key)
            if existing is not None and existing.completed:
                return ClaimResult.COMPLETED
            if existing is not None and existing.expires_at > now and existing.owner != owner:
                return ClaimResult.IN_PROGRESS
            self._claims[key] = _Claim(
                owner=owner,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
            return ClaimResult.CLAIMED

    async def complete(self, key: str, *, owner: str) -> None:
        async with self._lock:
            claim = self._owned_claim(key, owner)
            claim.completed = True
            claim.expires_at = datetime.max.replace(tzinfo=UTC)

    async def release(self, key: str, *, owner: str) -> None:
        async with self._lock:
            self._owned_claim(key, owner)
            del self._claims[key]

    def _owned_claim(self, key: str, owner: str) -> _Claim:
        claim = self._claims.get(key)
        if claim is None or claim.owner != owner:
            raise PermissionError(f"idempotency claim {key!r} is not owned by {owner!r}")
        return claim
