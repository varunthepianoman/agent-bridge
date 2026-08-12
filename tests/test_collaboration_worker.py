from __future__ import annotations

from typing import Any

from agent_bridge_bridge.collaboration_worker import CollaborationProjectionWorker
from agent_bridge_protocol.models import BridgeEnvelope


class Delivery:
    def __init__(self, envelope: BridgeEnvelope) -> None:
        self.envelope = envelope
        self.subject = "bridge.v1.inbox.role.receiver"
        self.acked = False
        self.nacked = False

    async def ack(self) -> None:
        self.acked = True

    async def nak(self, *, reason: str) -> None:
        assert reason == "collaboration_projection_failed"
        self.nacked = True


class Subscription:
    def __init__(self, delivery: Delivery) -> None:
        self.delivery = delivery

    async def fetch(self, **_options: Any) -> list[Delivery]:
        return [self.delivery]


async def test_projection_persists_before_ack() -> None:
    envelope = BridgeEnvelope(
        message_id="message-1",
        kind="message",
        sender={"kind": "role", "id": "sender"},
        destination={"kind": "role", "id": "receiver"},
        body={"unknown": {"future": True}},
        extensions={"custom.controller": {"topology": "peer"}},
    )
    delivery = Delivery(envelope)

    class Sink:
        async def ingest_envelope(self, received: BridgeEnvelope, *, subject: str) -> None:
            assert not delivery.acked
            assert received == envelope
            assert subject == delivery.subject

    count = await CollaborationProjectionWorker(Sink()).run_once(  # type: ignore[arg-type]
        Subscription(delivery)  # type: ignore[arg-type]
    )
    assert count == 1
    assert delivery.acked
    assert not delivery.nacked


async def test_projection_naks_failed_persistence() -> None:
    envelope = BridgeEnvelope(
        message_id="message-2",
        kind="message",
        sender={"kind": "role", "id": "sender"},
        destination={"kind": "role", "id": "receiver"},
    )
    delivery = Delivery(envelope)

    class FailingSink:
        async def ingest_envelope(self, received: BridgeEnvelope, *, subject: str) -> None:
            del received, subject
            raise RuntimeError("database unavailable")

    count = await CollaborationProjectionWorker(FailingSink()).run_once(  # type: ignore[arg-type]
        Subscription(delivery)  # type: ignore[arg-type]
    )
    assert count == 0
    assert delivery.nacked
    assert not delivery.acked
