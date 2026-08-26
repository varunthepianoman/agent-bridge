"""Durable NATS-to-mailbox delivery for selected conversations and rooms."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from agent_bridge_protocol.models import BridgeEnvelope, EndpointKind

from .core import AttentionStore, MailboxStore, MessageStore, RoomStore
from .repository import CatalogRepository


class Delivery(Protocol):
    subject: str
    envelope: BridgeEnvelope

    async def ack(self) -> None: ...

    async def in_progress(self) -> None: ...

    async def nak(self, *, reason: str) -> None: ...

    async def dead_letter(self, *, reason: str) -> Any: ...


class Subscription(Protocol):
    async def fetch(self, *, batch: int, timeout: float) -> Sequence[Delivery]: ...


class ConversationDeliveryWorker:
    """Materialize broker envelopes as durable mailbox deliveries.

    Ordinary Bridge messages never resume, start, or steer provider turns.  Provider
    mutation remains available only through the explicit turn APIs.
    """

    def __init__(
        self,
        *,
        repository: CatalogRepository,
        messages: MessageStore,
        mailbox: MailboxStore,
        rooms: RoomStore,
        attention: AttentionStore,
    ) -> None:
        self.repository = repository
        self.messages = messages
        self.mailbox = mailbox
        self.rooms = rooms
        self.attention = attention

    async def serve(self, subscription: Any) -> None:
        while True:
            deliveries = await subscription.fetch(batch=10, timeout=1.0)
            for delivery in deliveries:
                await self.handle(delivery)

    async def handle(self, delivery: Delivery) -> None:
        try:
            envelope = delivery.envelope
        except ValueError:
            await delivery.dead_letter(reason="invalid_envelope")
            return
        existing = self.messages.get(envelope.message_id)
        if existing is not None and existing["state"] == "delivered":
            await delivery.ack()
            return
        destination = envelope.destination
        try:
            if destination.kind == EndpointKind.CONVERSATION:
                self.messages.record_incoming(
                    envelope,
                    target_conversation_id=destination.id,
                    room_id=None,
                    subject=delivery.subject,
                )
                self._deliver_conversation(envelope, destination.id)
            elif destination.kind == EndpointKind.ROOM:
                self.messages.record_incoming(
                    envelope,
                    target_conversation_id=None,
                    room_id=destination.id,
                    subject=delivery.subject,
                )
                self._deliver_room(envelope, destination.id)
            else:
                await delivery.dead_letter(reason="unsupported_destination")
                return
        except (LookupError, ValueError) as exc:
            self.messages.set_state(envelope.message_id, "failed", error=str(exc))
            await delivery.dead_letter(reason="invalid_destination")
            return
        except Exception as exc:
            self.messages.set_state(envelope.message_id, "retrying", error=str(exc))
            await delivery.nak(reason="mailbox_delivery_failed")
            return
        self.messages.set_state(envelope.message_id, "delivered", delivery_route="mailbox")
        await delivery.ack()

    def _deliver_room(self, envelope: BridgeEnvelope, room_id: str) -> None:
        room = next((item for item in self.rooms.list() if item["room_id"] == room_id), None)
        if room is None:
            raise LookupError("room not found")
        for member in room["members"]:
            conversation_id = str(member["conversation_id"])
            mode = str(member["delivery_mode"])
            if mode == "mailbox":
                self._deliver_conversation(envelope, conversation_id)
                continue
            self.attention.create(
                category="update",
                kind=f"room_{mode}",
                title=f"New message in {room['name']}",
                detail=str(envelope.body.get("text", "")),
                conversation_id=conversation_id,
                correlation_id=envelope.correlation_id,
            )

    def _deliver_conversation(self, envelope: BridgeEnvelope, conversation_id: str) -> None:
        row = self.repository.get(conversation_id)
        if row is None or not row.selected:
            raise LookupError("selected conversation not found")
        self.mailbox.enqueue(envelope.message_id, [conversation_id])
