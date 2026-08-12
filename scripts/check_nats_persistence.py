"""Publish or consume the fixed record used by the broker restart smoke test."""

from __future__ import annotations

import argparse
import asyncio

from agent_bridge_bridge.subjects import inbox_subject
from agent_bridge_bridge.transport import JetStreamSettings, JetStreamTransport
from agent_bridge_protocol.models import BridgeEnvelope, EndpointKind, EndpointRef, MessageKind


def envelope() -> BridgeEnvelope:
    return BridgeEnvelope(
        message_id="m4-config-persistence",
        kind=MessageKind.REQUEST,
        sender=EndpointRef(kind=EndpointKind.ROLE, id="persistence-check"),
        destination=EndpointRef(kind=EndpointKind.NODE, id="node-a"),
    )


async def publish(url: str) -> None:
    async with JetStreamTransport(JetStreamSettings(servers=(url,))) as transport:
        await transport.provision_streams()
        await transport.publish(envelope())


async def consume(url: str) -> None:
    expected = envelope()
    async with JetStreamTransport(JetStreamSettings(servers=(url,))) as transport:
        subscription = await transport.subscribe(
            inbox_subject(expected.destination), durable_name="m4-persistence-check-final"
        )
        deliveries = await subscription.fetch(timeout=3)
        assert len(deliveries) == 1
        assert deliveries[0].envelope.message_id == expected.message_id
        assert deliveries[0].envelope.destination == expected.destination
        await deliveries[0].ack()
    print("NATS configured file-storage restart smoke: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("publish", "consume"))
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    asyncio.run(publish(args.url) if args.operation == "publish" else consume(args.url))


if __name__ == "__main__":
    main()
