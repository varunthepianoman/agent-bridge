from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_bridge_bridge.execution_store import SQLiteExecutionStore
from agent_bridge_bridge.runners import CancellationToken, ExecutionCancelled
from agent_bridge_bridge.worker import ControlWorker
from agent_bridge_protocol.models import (
    BridgeEnvelope,
    EndpointKind,
    EndpointRef,
    MessageKind,
)


class FakeDelivery:
    def __init__(self, envelope: BridgeEnvelope) -> None:
        self.envelope = envelope
        self.acked = False
        self.dead_reason: str | None = None

    async def ack(self) -> None:
        self.acked = True

    async def dead_letter(self, *, reason: str) -> Any:
        self.dead_reason = reason


def control() -> BridgeEnvelope:
    return BridgeEnvelope(
        message_id="control-1",
        kind=MessageKind.CONTROL,
        sender=EndpointRef(kind=EndpointKind.ROLE, id="role-user"),
        destination=EndpointRef(kind=EndpointKind.NODE, id="node-a"),
        body={"operation": "cancel", "execution_id": "exec-1", "reason": "stop now"},
    )


async def test_control_worker_durably_cancels_before_ack_and_is_idempotent(
    tmp_path: Path,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "runner.sqlite3")
    worker = ControlWorker(store)
    first = FakeDelivery(control())
    await worker.process(first)  # type: ignore[arg-type]
    assert first.acked
    assert await store.cancellation_reason("exec-1") == "stop now"

    duplicate = FakeDelivery(control())
    await worker.process(duplicate)  # type: ignore[arg-type]
    assert duplicate.acked
    token = CancellationToken(store, "exec-1")
    try:
        await token.raise_if_cancelled()
    except ExecutionCancelled as error:
        assert str(error) == "stop now"
    else:
        raise AssertionError("next execution did not observe durable cancellation")
    store.close()
