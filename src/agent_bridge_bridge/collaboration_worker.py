"""Persist-before-ack consumer for generic collaboration history projections."""

from __future__ import annotations

from typing import Protocol

from agent_bridge_protocol.models import BridgeEnvelope

from .transport import BridgeSubscription


class CollaborationEnvelopeSink(Protocol):
    async def ingest_envelope(self, envelope: BridgeEnvelope, *, subject: str) -> None: ...


class CollaborationProjectionWorker:
    """Materialize inbound envelopes without becoming their delivery authority."""

    def __init__(self, sink: CollaborationEnvelopeSink) -> None:
        self.sink = sink

    async def run_once(
        self,
        subscription: BridgeSubscription,
        *,
        batch: int = 10,
        timeout: float = 1,
    ) -> int:
        deliveries = await subscription.fetch(batch=batch, timeout=timeout)
        persisted = 0
        for delivery in deliveries:
            try:
                envelope = delivery.envelope
                await self.sink.ingest_envelope(envelope, subject=delivery.subject)
            except Exception:
                await delivery.nak(reason="collaboration_projection_failed")
                continue
            await delivery.ack()
            persisted += 1
        return persisted
