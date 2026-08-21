"""Durable NATS-to-provider delivery for selected conversations and rooms."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from agent_bridge_protocol.models import BridgeEnvelope, EndpointKind
from agent_bridge_providers import ActiveTurnDeliveryState

from .core import AttentionStore, MessageStore, RoomStore
from .nodes import NodeStore
from .repository import CatalogRepository
from .runtime import ConversationRuntime, ConversationWriterBusy


class Delivery(Protocol):
    subject: str
    envelope: BridgeEnvelope

    async def ack(self) -> None: ...

    async def in_progress(self) -> None: ...

    async def nak(self, *, reason: str) -> None: ...

    async def dead_letter(self, *, reason: str) -> Any: ...


class Subscription(Protocol):
    async def fetch(self, *, batch: int, timeout: float) -> Sequence[Delivery]: ...


class DeliveryOutcomeUncertain(RuntimeError):
    """A steer may have been accepted, so retrying could duplicate the message."""


class ConversationDeliveryWorker:
    def __init__(
        self,
        *,
        repository: CatalogRepository,
        messages: MessageStore,
        rooms: RoomStore,
        attention: AttentionStore,
        nodes: NodeStore,
        runtime: ConversationRuntime,
        local_node_id: str,
        writer_retry_seconds: float = 10.0,
    ) -> None:
        self.repository = repository
        self.messages = messages
        self.rooms = rooms
        self.attention = attention
        self.nodes = nodes
        self.runtime = runtime
        self.local_node_id = local_node_id
        self.writer_retry_seconds = writer_retry_seconds

    async def serve(self, subscription: Any) -> None:
        # A provider can retain a conversation's writer lock while its native UI
        # is open. Keep that delivery leased without blocking unrelated messages.
        async with asyncio.TaskGroup() as tasks:
            while True:
                deliveries = await subscription.fetch(batch=10, timeout=1.0)
                for delivery in deliveries:
                    tasks.create_task(self.handle(delivery))

    async def handle(self, delivery: Delivery) -> None:
        try:
            envelope = delivery.envelope
        except ValueError:
            await delivery.dead_letter(reason="invalid_envelope")
            return
        existing = self.messages.get(envelope.message_id)
        if existing is not None and existing["state"] in {
            "delivered",
            "delivery_uncertain",
            "queued_remote",
        }:
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
                await self._deliver_conversation(envelope, destination.id, delivery)
            elif destination.kind == EndpointKind.ROOM:
                self.messages.record_incoming(
                    envelope,
                    target_conversation_id=None,
                    room_id=destination.id,
                    subject=delivery.subject,
                )
                await self._deliver_room(envelope, destination.id, delivery)
            else:
                await delivery.dead_letter(reason="unsupported_destination")
                return
        except DeliveryOutcomeUncertain:
            await delivery.ack()
            return
        except (LookupError, ValueError) as exc:
            self.messages.set_state(envelope.message_id, "failed", error=str(exc))
            await delivery.dead_letter(reason="invalid_destination")
            return
        except Exception as exc:
            self.messages.set_state(envelope.message_id, "retrying", error=str(exc))
            await delivery.nak(reason="provider_delivery_failed")
            return
        current = self.messages.get(envelope.message_id)
        if current is None or current["state"] != "queued_remote":
            self.messages.set_state(envelope.message_id, "delivered")
        await delivery.ack()

    async def _deliver_room(
        self, envelope: BridgeEnvelope, room_id: str, delivery: Delivery
    ) -> None:
        room = next((item for item in self.rooms.list() if item["room_id"] == room_id), None)
        if room is None:
            raise LookupError("room not found")
        for member in room["members"]:
            conversation_id = str(member["conversation_id"])
            mode = str(member["delivery_mode"])
            if mode == "wake":
                await self._deliver_conversation(envelope, conversation_id, delivery)
            else:
                self.attention.create(
                    category="update",
                    kind=f"room_{mode}",
                    title=f"New message in {room['name']}",
                    detail=str(envelope.body.get("text", "")),
                    conversation_id=conversation_id,
                    correlation_id=envelope.correlation_id,
                )

    async def _deliver_conversation(
        self,
        envelope: BridgeEnvelope,
        conversation_id: str,
        delivery: Delivery,
    ) -> None:
        row = self.repository.get(conversation_id)
        if row is None or not row.selected:
            raise LookupError("selected conversation not found")
        if row.delivery_mode != "direct":
            raise ValueError(f"conversation delivery mode is {row.delivery_mode}")
        prompt = self._prompt(envelope)
        if row.node_id != self.local_node_id:
            self.nodes.queue_command(
                node_id=row.node_id,
                kind="deliver_turn",
                conversation_id=row.conversation_id,
                payload={
                    "provider": row.provider,
                    "provider_thread_id": row.provider_thread_id,
                    "workspace": row.cwd,
                    "environment_id": row.environment_id,
                    "prompt": prompt,
                    "message_id": envelope.message_id,
                    "correlation_id": envelope.correlation_id,
                },
            )
            route = (
                "queued_fallback"
                if envelope.delivery.strategy == "steer-or-queue"
                else "new_turn"
            )
            self.messages.set_state(
                envelope.message_id,
                "queued_remote",
                delivery_route=route,
            )
            return
        heartbeat = asyncio.create_task(self._heartbeat(delivery))
        try:
            while True:
                try:
                    await self.runtime.turn(
                        provider=row.provider,
                        provider_thread_id=row.provider_thread_id,
                        cwd=row.cwd or ".",
                        prompt=prompt,
                    )
                except ConversationWriterBusy as exc:
                    if envelope.delivery.strategy == "steer-or-queue":
                        result = await self.runtime.deliver_active_turn(
                            provider=row.provider,
                            provider_thread_id=row.provider_thread_id,
                            cwd=row.cwd or ".",
                            prompt=prompt,
                            message_id=envelope.message_id,
                        )
                        if result.state == ActiveTurnDeliveryState.DELIVERED:
                            self.messages.set_state(
                                envelope.message_id,
                                "received",
                                delivery_route="steered",
                            )
                            break
                        if result.state == ActiveTurnDeliveryState.UNCERTAIN:
                            confirmed = await self.runtime.wait_for_client_message(
                                row.provider_thread_id,
                                envelope.message_id,
                            )
                            if confirmed:
                                self.messages.set_state(
                                    envelope.message_id,
                                    "received",
                                    delivery_route="steered",
                                )
                                break
                            detail = result.detail or "Codex steering outcome is unknown"
                            self.messages.set_state(
                                envelope.message_id,
                                "delivery_uncertain",
                                error=detail,
                                delivery_route="steered",
                            )
                            self.attention.create(
                                category="needs_attention",
                                kind="delivery_uncertain",
                                title="Bridge message steering outcome is unknown",
                                detail=detail,
                                conversation_id=row.conversation_id,
                                correlation_id=envelope.correlation_id,
                            )
                            raise DeliveryOutcomeUncertain(detail) from exc
                        fallback_detail = result.detail or str(exc)
                        self.messages.set_state(
                            envelope.message_id,
                            "waiting_for_provider",
                            error=f"Steering unavailable; queued for delivery: {fallback_detail}",
                            delivery_route="queued_fallback",
                        )
                    else:
                        self.messages.set_state(
                            envelope.message_id,
                            "waiting_for_provider",
                            error=(
                                "Queued until the native conversation releases its writer: "
                                f"{exc}"
                            ),
                        )
                    await asyncio.sleep(self.writer_retry_seconds)
                    continue
                self.messages.set_state(
                    envelope.message_id,
                    "received",
                    delivery_route="new_turn",
                )
                break
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    @staticmethod
    async def _heartbeat(delivery: Delivery) -> None:
        while True:
            await asyncio.sleep(20)
            await delivery.in_progress()

    @staticmethod
    def _prompt(envelope: BridgeEnvelope) -> str:
        body: Mapping[str, Any] = envelope.body
        return "\n".join(
            (
                "[Agent Bridge message — authenticated by your private Bridge]",
                f"From: {envelope.sender.kind}:{envelope.sender.id}",
                f"Message: {envelope.message_id}",
                f"Correlation: {envelope.correlation_id or envelope.message_id}",
                f"Operation: {body.get('operation', 'message')}",
                "",
                str(body.get("text", "")),
            )
        )
