from types import SimpleNamespace

import pytest

from agent_bridge_catalog.delivery import ConversationDeliveryWorker
from agent_bridge_protocol.models import (
    BridgeEnvelope,
    DeliveryPolicy,
    EndpointKind,
    EndpointRef,
    MessageKind,
)


class Messages:
    def __init__(self) -> None:
        self.item: dict[str, object] | None = None
        self.states: list[tuple[str, str, str | None, str | None]] = []

    def get(self, _message_id: str) -> dict[str, object] | None:
        return self.item

    def record_incoming(self, envelope: BridgeEnvelope, **_fields: object) -> None:
        self.item = {"message_id": envelope.message_id, "state": "published"}

    def set_state(
        self,
        message_id: str,
        state: str,
        *,
        error: str | None = None,
        delivery_route: str | None = None,
    ) -> None:
        self.states.append((message_id, state, error, delivery_route))
        if self.item is not None:
            self.item["state"] = state


class Mailbox:
    def __init__(self) -> None:
        self.deliveries: set[tuple[str, str]] = set()

    def enqueue(self, message_id: str, recipients: list[str]) -> list[dict[str, str]]:
        for recipient in recipients:
            self.deliveries.add((message_id, recipient))
        return [
            {"message_id": message_id, "recipient_conversation_id": recipient}
            for recipient in recipients
        ]


class Attention:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def create(self, **fields: object) -> None:
        self.items.append(fields)


class HandleDelivery:
    subject = "bridge.v1.inbox.conversation.target"

    def __init__(self, envelope: BridgeEnvelope) -> None:
        self.envelope = envelope
        self.acked = False
        self.dead_letter_reason: str | None = None
        self.nak_reason: str | None = None

    async def ack(self) -> None:
        self.acked = True

    async def in_progress(self) -> None:
        pass

    async def nak(self, *, reason: str) -> None:
        self.nak_reason = reason

    async def dead_letter(self, *, reason: str) -> None:
        self.dead_letter_reason = reason


def _conversation(conversation_id: str) -> SimpleNamespace:
    return SimpleNamespace(selected=True, conversation_id=conversation_id)


def _worker(
    *,
    rooms: list[dict[str, object]] | None = None,
    selected: set[str] | None = None,
) -> tuple[ConversationDeliveryWorker, Messages, Mailbox, Attention]:
    messages = Messages()
    mailbox = Mailbox()
    attention = Attention()
    selected = selected or {"target"}
    repository = SimpleNamespace(
        get=lambda conversation_id: (
            _conversation(conversation_id) if conversation_id in selected else None
        )
    )
    worker = ConversationDeliveryWorker(
        repository=repository,
        messages=messages,  # type: ignore[arg-type]
        mailbox=mailbox,  # type: ignore[arg-type]
        rooms=SimpleNamespace(list=lambda: rooms or []),
        attention=attention,  # type: ignore[arg-type]
    )
    return worker, messages, mailbox, attention


def _envelope(
    *,
    destination_kind: EndpointKind = EndpointKind.CONVERSATION,
    destination_id: str = "target",
    strategy: str = "mailbox",
) -> BridgeEnvelope:
    return BridgeEnvelope(
        message_id="message-1",
        kind=MessageKind.MESSAGE,
        sender=EndpointRef(kind=EndpointKind.CONVERSATION, id="source"),
        destination=EndpointRef(kind=destination_kind, id=destination_id),
        body={"text": "hello"},
        delivery=DeliveryPolicy(strategy=strategy),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("strategy", ["mailbox", "queue", "steer-or-queue"])
async def test_direct_messages_are_materialized_without_provider_calls(strategy: str) -> None:
    worker, messages, mailbox, _attention = _worker()
    delivery = HandleDelivery(_envelope(strategy=strategy))

    await worker.handle(delivery)

    assert delivery.acked is True
    assert mailbox.deliveries == {("message-1", "target")}
    assert messages.states[-1] == ("message-1", "delivered", None, "mailbox")
    assert not hasattr(worker, "runtime")
    assert not hasattr(worker, "nodes")


async def test_redelivery_is_idempotent_after_transport_delivery() -> None:
    worker, messages, mailbox, _attention = _worker()

    await worker.handle(HandleDelivery(_envelope()))
    second = HandleDelivery(_envelope())
    await worker.handle(second)

    assert second.acked is True
    assert mailbox.deliveries == {("message-1", "target")}
    assert len(messages.states) == 1


async def test_room_fanout_uses_mailboxes_and_notifications() -> None:
    room = {
        "room_id": "room-1",
        "name": "Operations",
        "members": [
            {"conversation_id": "inner-a", "delivery_mode": "mailbox"},
            {"conversation_id": "outer", "delivery_mode": "notify"},
            {"conversation_id": "digest", "delivery_mode": "digest"},
        ],
    }
    worker, _messages, mailbox, attention = _worker(
        rooms=[room], selected={"inner-a", "outer", "digest"}
    )
    delivery = HandleDelivery(
        _envelope(destination_kind=EndpointKind.ROOM, destination_id="room-1")
    )

    await worker.handle(delivery)

    assert delivery.acked is True
    assert mailbox.deliveries == {("message-1", "inner-a")}
    assert [item["kind"] for item in attention.items] == ["room_notify", "room_digest"]


async def test_unselected_destination_is_dead_lettered() -> None:
    worker, messages, mailbox, _attention = _worker(selected={"another"})
    delivery = HandleDelivery(_envelope())

    await worker.handle(delivery)

    assert delivery.dead_letter_reason == "invalid_destination"
    assert mailbox.deliveries == set()
    assert messages.states[-1][1] == "failed"
