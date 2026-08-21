from types import SimpleNamespace

from agent_bridge_catalog.delivery import ConversationDeliveryWorker
from agent_bridge_catalog.runtime import ConversationRuntime, ConversationWriterBusy
from agent_bridge_protocol.models import (
    BridgeEnvelope,
    DeliveryPolicy,
    EndpointKind,
    EndpointRef,
    MessageKind,
)
from agent_bridge_providers import ActiveTurnDeliveryResult, ActiveTurnDeliveryState


class Runtime:
    def __init__(self) -> None:
        self.calls = 0

    async def turn(self, **_kwargs: object) -> None:
        self.calls += 1
        if self.calls == 1:
            raise ConversationWriterBusy("thread already has an active writer")


class Messages:
    def __init__(self) -> None:
        self.states: list[tuple[str, str, str | None]] = []

    def set_state(
        self,
        message_id: str,
        state: str,
        *,
        error: str | None = None,
        delivery_route: str | None = None,
    ) -> None:
        del delivery_route
        self.states.append((message_id, state, error))


class Delivery:
    async def in_progress(self) -> None:
        pass


async def test_writer_owned_conversation_stays_queued_then_delivers() -> None:
    runtime = Runtime()
    messages = Messages()
    repository = SimpleNamespace(
        get=lambda _conversation_id: SimpleNamespace(
            selected=True,
            delivery_mode="direct",
            node_id="local",
            conversation_id="target",
            provider="codex",
            provider_thread_id="thread",
            cwd="/tmp",
            environment_id="host",
        )
    )
    worker = ConversationDeliveryWorker(
        repository=repository,
        messages=messages,
        rooms=SimpleNamespace(),
        attention=SimpleNamespace(),
        nodes=SimpleNamespace(),
        runtime=runtime,
        local_node_id="local",
        writer_retry_seconds=0,
    )
    envelope = BridgeEnvelope(
        message_id="message-1",
        kind=MessageKind.MESSAGE,
        sender=EndpointRef(kind=EndpointKind.CONVERSATION, id="source"),
        destination=EndpointRef(kind=EndpointKind.CONVERSATION, id="target"),
        body={"text": "hello"},
    )

    await worker._deliver_conversation(envelope, "target", Delivery())

    assert runtime.calls == 2
    assert messages.states == [
        (
            "message-1",
            "waiting_for_provider",
            "Queued until the native conversation releases its writer: "
            "thread already has an active writer",
        ),
        ("message-1", "received", None),
    ]


class SteeringRuntime(Runtime):
    def __init__(self, state: ActiveTurnDeliveryState, *, confirmed: bool = False) -> None:
        super().__init__()
        self.state = state
        self.confirmed = confirmed

    async def deliver_active_turn(self, **_kwargs: object) -> ActiveTurnDeliveryResult:
        return ActiveTurnDeliveryResult(self.state, "steer detail")

    async def wait_for_client_message(self, *_args: object, **_kwargs: object) -> bool:
        return self.confirmed


class RoutedMessages:
    def __init__(self) -> None:
        self.states: list[tuple[str, str, str | None, str | None]] = []

    def get(self, _message_id: str) -> None:
        return None

    def record_incoming(self, *_args: object, **_kwargs: object) -> None:
        return None

    def set_state(
        self,
        message_id: str,
        state: str,
        *,
        error: str | None = None,
        delivery_route: str | None = None,
    ) -> None:
        self.states.append((message_id, state, error, delivery_route))


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

    async def ack(self) -> None:
        self.acked = True

    async def in_progress(self) -> None:
        pass

    async def nak(self, *, reason: str) -> None:
        raise AssertionError(reason)

    async def dead_letter(self, *, reason: str) -> None:
        raise AssertionError(reason)


def _steering_worker(
    runtime: SteeringRuntime, messages: RoutedMessages, attention: Attention
) -> ConversationDeliveryWorker:
    repository = SimpleNamespace(
        get=lambda _conversation_id: SimpleNamespace(
            selected=True,
            delivery_mode="direct",
            node_id="local",
            conversation_id="target",
            provider="codex",
            provider_thread_id="thread",
            cwd="/tmp",
            environment_id="host",
        )
    )
    return ConversationDeliveryWorker(
        repository=repository,
        messages=messages,  # type: ignore[arg-type]
        rooms=SimpleNamespace(),
        attention=attention,  # type: ignore[arg-type]
        nodes=SimpleNamespace(),
        runtime=runtime,  # type: ignore[arg-type]
        local_node_id="local",
        writer_retry_seconds=0,
    )


def _steering_envelope() -> BridgeEnvelope:
    return BridgeEnvelope(
        message_id="message-steer",
        kind=MessageKind.MESSAGE,
        sender=EndpointRef(kind=EndpointKind.CONVERSATION, id="source"),
        destination=EndpointRef(kind=EndpointKind.CONVERSATION, id="target"),
        body={"text": "hello"},
        delivery=DeliveryPolicy(strategy="steer-or-queue"),
    )


async def test_busy_codex_turn_is_steered_and_acknowledged() -> None:
    messages = RoutedMessages()
    attention = Attention()
    worker = _steering_worker(
        SteeringRuntime(ActiveTurnDeliveryState.DELIVERED), messages, attention
    )
    delivery = HandleDelivery(_steering_envelope())

    await worker.handle(delivery)

    assert delivery.acked is True
    assert ("message-steer", "received", None, "steered") in messages.states
    assert messages.states[-1][:2] == ("message-steer", "delivered")
    assert attention.items == []


async def test_uncertain_steer_confirmed_in_transcript_is_delivered() -> None:
    messages = RoutedMessages()
    attention = Attention()
    worker = _steering_worker(
        SteeringRuntime(ActiveTurnDeliveryState.UNCERTAIN, confirmed=True),
        messages,
        attention,
    )
    delivery = HandleDelivery(_steering_envelope())

    await worker.handle(delivery)

    assert delivery.acked is True
    assert messages.states[-1][:2] == ("message-steer", "delivered")
    assert attention.items == []


async def test_uncertain_unconfirmed_steer_is_not_retried() -> None:
    messages = RoutedMessages()
    attention = Attention()
    worker = _steering_worker(
        SteeringRuntime(ActiveTurnDeliveryState.UNCERTAIN), messages, attention
    )
    delivery = HandleDelivery(_steering_envelope())

    await worker.handle(delivery)

    assert delivery.acked is True
    assert messages.states[-1] == (
        "message-steer",
        "delivery_uncertain",
        "steer detail",
        "steered",
    )
    assert attention.items[0]["kind"] == "delivery_uncertain"


async def test_unavailable_steer_falls_back_then_delivers_new_turn() -> None:
    messages = RoutedMessages()
    attention = Attention()
    worker = _steering_worker(
        SteeringRuntime(ActiveTurnDeliveryState.UNAVAILABLE), messages, attention
    )
    delivery = HandleDelivery(_steering_envelope())

    await worker.handle(delivery)

    assert delivery.acked is True
    assert (
        "message-steer",
        "waiting_for_provider",
        "Steering unavailable; queued for delivery: steer detail",
        "queued_fallback",
    ) in messages.states
    assert ("message-steer", "received", None, "new_turn") in messages.states
    assert messages.states[-1][:2] == ("message-steer", "delivered")


async def test_claude_active_turn_delivery_is_not_implemented() -> None:
    runtime = ConversationRuntime()
    try:
        result = await runtime.deliver_active_turn(
            provider="claude",
            provider_thread_id="session",
            cwd="/tmp",
            prompt="prompt",
            message_id="message",
        )
    finally:
        await runtime.close()

    assert result.state == ActiveTurnDeliveryState.UNAVAILABLE
    assert "claude" in str(result.detail)


async def test_remote_steer_or_queue_is_queued_with_fallback_route() -> None:
    messages = RoutedMessages()
    queued: list[dict[str, object]] = []
    repository = SimpleNamespace(
        get=lambda _conversation_id: SimpleNamespace(
            selected=True,
            delivery_mode="direct",
            node_id="remote",
            conversation_id="target",
            provider="codex",
            provider_thread_id="thread",
            cwd="/tmp",
            environment_id="host",
        )
    )
    nodes = SimpleNamespace(queue_command=lambda **fields: queued.append(fields))
    worker = ConversationDeliveryWorker(
        repository=repository,
        messages=messages,  # type: ignore[arg-type]
        rooms=SimpleNamespace(),
        attention=Attention(),  # type: ignore[arg-type]
        nodes=nodes,
        runtime=SteeringRuntime(ActiveTurnDeliveryState.DELIVERED),  # type: ignore[arg-type]
        local_node_id="local",
    )

    await worker._deliver_conversation(_steering_envelope(), "target", Delivery())

    assert len(queued) == 1
    assert messages.states[-1] == (
        "message-steer",
        "queued_remote",
        None,
        "queued_fallback",
    )
