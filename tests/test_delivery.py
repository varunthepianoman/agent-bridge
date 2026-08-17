from types import SimpleNamespace

from agent_bridge_catalog.delivery import ConversationDeliveryWorker
from agent_bridge_catalog.runtime import ConversationWriterBusy
from agent_bridge_protocol.models import BridgeEnvelope, EndpointKind, EndpointRef, MessageKind


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

    def set_state(self, message_id: str, state: str, *, error: str | None = None) -> None:
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
        )
    ]
