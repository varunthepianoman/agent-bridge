"""Topology-neutral collaboration primitives over the durable Bridge transport."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from agent_bridge_protocol.models import (
    ArtifactRef,
    BridgeEnvelope,
    DeliveryPolicy,
    EndpointKind,
    EndpointRef,
    MessageKind,
)

from .subjects import (
    capability_subject,
    event_subject,
    inbox_subject,
    result_subject,
    room_subject,
    validate_token,
)
from .transport import (
    BridgeSubscription,
    JetStreamTransport,
    PublishedMessage,
)


@dataclass(frozen=True)
class CollaborationPublish:
    envelope: BridgeEnvelope
    acknowledgement: PublishedMessage


@dataclass(frozen=True)
class PendingRequest:
    envelope: BridgeEnvelope
    reply_subject: str


class CollaborationClient:
    """Convenience API whose routing does not imply a management topology.

    Roles, provider conversations, nodes, and alternate policy controllers all use
    the same endpoint inboxes. Capability, room, and event routes are explicit
    opt-in fan-out mechanisms; none require a coordinator to be online.
    """

    def __init__(self, transport: JetStreamTransport, *, identity: EndpointRef) -> None:
        self.transport = transport
        self.identity = identity

    async def send(
        self,
        destination: EndpointRef,
        body: dict[str, Any],
        *,
        kind: MessageKind = MessageKind.MESSAGE,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        reply_to: EndpointRef | None = None,
        work_id: str | None = None,
        delivery: DeliveryPolicy | None = None,
        artifacts: list[ArtifactRef] | None = None,
        extensions: dict[str, Any] | None = None,
        subject: str | None = None,
    ) -> CollaborationPublish:
        envelope = BridgeEnvelope(
            message_id=_identity("msg"),
            kind=kind,
            sender=self.identity,
            destination=destination,
            body=body,
            correlation_id=correlation_id,
            causation_id=causation_id,
            reply_to=reply_to,
            work_id=work_id,
            delivery=delivery or DeliveryPolicy(),
            artifacts=artifacts or [],
            extensions=extensions or {},
        )
        acknowledgement = await self.transport.publish(envelope, subject=subject)
        return CollaborationPublish(envelope=envelope, acknowledgement=acknowledgement)

    async def request(
        self,
        destination: EndpointRef,
        body: dict[str, Any],
        *,
        reply_to: EndpointRef | None = None,
        work_id: str | None = None,
        delivery: DeliveryPolicy | None = None,
        artifacts: list[ArtifactRef] | None = None,
        extensions: dict[str, Any] | None = None,
    ) -> PendingRequest:
        correlation_id = _identity("corr")
        published = await self.send(
            destination,
            body,
            kind=MessageKind.REQUEST,
            correlation_id=correlation_id,
            reply_to=reply_to or self.identity,
            work_id=work_id,
            delivery=delivery,
            artifacts=artifacts,
            extensions=extensions,
        )
        return PendingRequest(
            envelope=published.envelope,
            reply_subject=result_subject(correlation_id),
        )

    async def fan_out(
        self,
        destinations: list[EndpointRef],
        body: dict[str, Any],
        *,
        kind: MessageKind = MessageKind.MESSAGE,
        work_id: str | None = None,
        causation_id: str | None = None,
        extensions: dict[str, Any] | None = None,
    ) -> list[CollaborationPublish]:
        """Publish unique children so stream-wide de-duplication cannot drop a target."""

        if not destinations:
            raise ValueError("fan-out requires at least one destination")
        correlation_id = _identity("corr")
        fanout_id = _identity("fanout")
        fanout_extensions = {
            **(extensions or {}),
            "agent_bridge.fanout_group_id": fanout_id,
        }
        return list(
            await asyncio.gather(
                *(
                    self.send(
                        destination,
                        body,
                        kind=kind,
                        correlation_id=correlation_id,
                        causation_id=causation_id,
                        work_id=work_id,
                        extensions=fanout_extensions,
                    )
                    for destination in destinations
                )
            )
        )

    async def reply(
        self,
        request: BridgeEnvelope,
        body: dict[str, Any],
        *,
        kind: MessageKind = MessageKind.RESPONSE,
        extensions: dict[str, Any] | None = None,
    ) -> CollaborationPublish:
        if request.kind not in {
            MessageKind.REQUEST,
            MessageKind.PROPOSAL,
            MessageKind.CRITIQUE,
            MessageKind.REVISION,
        }:
            raise ValueError("only a request or collaboration turn can be replied to")
        if kind not in {
            MessageKind.RESPONSE,
            MessageKind.CRITIQUE,
            MessageKind.REVISION,
            MessageKind.ACCEPTANCE,
        }:
            raise ValueError("reply kind must be response, critique, revision, or acceptance")
        correlation_id = request.correlation_id or request.message_id
        return await self.send(
            request.reply_to or request.sender,
            body,
            kind=kind,
            correlation_id=correlation_id,
            causation_id=request.message_id,
            work_id=request.work_id,
            extensions=extensions,
            subject=result_subject(correlation_id),
        )

    async def dispatch_capability(
        self,
        capability_id: str,
        body: dict[str, Any],
        **options: Any,
    ) -> CollaborationPublish:
        destination = EndpointRef(kind=EndpointKind.CAPABILITY, id=capability_id)
        return await self.send(
            destination,
            body,
            subject=capability_subject(capability_id),
            **options,
        )

    async def publish_event(
        self,
        topic: str,
        body: dict[str, Any],
        **options: Any,
    ) -> CollaborationPublish:
        destination = EndpointRef(kind=EndpointKind.ROOM, id=topic)
        return await self.send(
            destination,
            body,
            kind=MessageKind.EVENT,
            subject=event_subject(topic),
            **options,
        )

    async def publish_room(
        self,
        room_id: str,
        body: dict[str, Any],
        **options: Any,
    ) -> CollaborationPublish:
        destination = EndpointRef(kind=EndpointKind.ROOM, id=room_id)
        return await self.send(destination, body, subject=room_subject(room_id), **options)

    async def subscribe_inbox(
        self,
        *,
        consumer_id: str,
        identity: EndpointRef | None = None,
        ack_wait_seconds: float = 60,
    ) -> BridgeSubscription:
        selected_identity = identity or self.identity
        return await self.transport.subscribe(
            inbox_subject(selected_identity),
            durable_name=_durable(
                "inbox", str(selected_identity.kind), selected_identity.id, consumer_id
            ),
            ack_wait_seconds=ack_wait_seconds,
        )

    async def subscribe_capability(
        self,
        capability_id: str,
        *,
        worker_group: str,
        ack_wait_seconds: float = 60,
    ) -> BridgeSubscription:
        """Workers in one group share a durable and compete for each request."""

        return await self.transport.subscribe(
            capability_subject(capability_id),
            durable_name=_durable("capability", capability_id, worker_group),
            ack_wait_seconds=ack_wait_seconds,
        )

    async def subscribe_room(
        self,
        room_id: str,
        *,
        participant_id: str,
        ack_wait_seconds: float = 60,
    ) -> BridgeSubscription:
        """Each participant gets its own offline-capable room replay cursor."""

        return await self.transport.subscribe(
            room_subject(room_id),
            durable_name=_durable("room", room_id, participant_id),
            ack_wait_seconds=ack_wait_seconds,
        )

    async def subscribe_events(
        self,
        topic: str,
        *,
        subscriber_id: str,
        ack_wait_seconds: float = 60,
    ) -> BridgeSubscription:
        return await self.transport.subscribe(
            event_subject(topic),
            durable_name=_durable("event", topic, subscriber_id),
            ack_wait_seconds=ack_wait_seconds,
        )

    async def subscribe_replies(
        self,
        pending: PendingRequest,
        *,
        subscriber_id: str,
        ack_wait_seconds: float = 60,
    ) -> BridgeSubscription:
        correlation_id = pending.envelope.correlation_id
        assert correlation_id is not None
        return await self.transport.subscribe(
            pending.reply_subject,
            durable_name=_durable("reply", correlation_id, subscriber_id),
            ack_wait_seconds=ack_wait_seconds,
        )


def _identity(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _durable(*parts: str) -> str:
    validated = [validate_token(part, label="consumer identity") for part in parts]
    candidate = "-".join(validated)
    if len(candidate) <= 128:
        return candidate
    digest = hashlib.sha256(candidate.encode()).hexdigest()[:16]
    return f"{candidate[:111]}-{digest}"
