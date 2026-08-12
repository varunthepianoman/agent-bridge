"""Milestone 9 acceptance tests for the ABB workflow migration.

These tests intentionally use only public Bridge contracts.  They protect the
architectural boundary: ABB is an ordinary capability, while JetStream and the
execution worker own durable delivery and exactly-once side-effect handling.
"""

from __future__ import annotations

import importlib
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from agent_bridge_bridge.execution_store import SQLiteExecutionStore
from agent_bridge_bridge.runners import (
    ROBOT_TEST_CAPABILITY,
    ExecutionDispatcher,
    RegisteredCapabilityRunner,
    RunnerOutput,
)
from agent_bridge_bridge.subjects import capability_subject, subject_for
from agent_bridge_bridge.worker import ExecutionWorker, WorkerSettings
from agent_bridge_protocol.models import (
    BridgeEnvelope,
    EndpointKind,
    EndpointRef,
    ExecutionOperation,
    ExecutionRequest,
    MessageKind,
)

REPOSITORY_ROOT = Path(__file__).parents[1]


class _UnusedRunner:
    async def run(self, *_args: object, **_kwargs: object) -> RunnerOutput:
        raise AssertionError("the ABB request must be dispatched to the capability runner")


class _RecordingTransport:
    def __init__(self, events: list[tuple[str, str]]) -> None:
        self.events = events
        self.results: list[tuple[BridgeEnvelope, str | None]] = []

    async def publish(
        self, envelope: BridgeEnvelope, *, subject: str | None = None
    ) -> object:
        self.events.append(("publish", envelope.message_id))
        self.results.append((envelope, subject))
        return object()


class _Delivery:
    def __init__(
        self,
        envelope: BridgeEnvelope,
        events: list[tuple[str, str]],
        *,
        delivery_count: int,
    ) -> None:
        self.envelope = envelope
        self.events = events
        self.delivery_count = delivery_count
        self.settled = False

    async def ack(self) -> None:
        self.events.append(("ack", self.envelope.message_id))
        self.settled = True

    async def nak(
        self, *, delay_seconds: float | None = None, reason: str | None = None
    ) -> None:
        del delay_seconds, reason
        raise AssertionError("successful ABB execution must not be negatively acknowledged")

    async def dead_letter(self, *, reason: str) -> None:
        raise AssertionError(f"successful ABB execution was dead-lettered: {reason}")

    async def in_progress(self) -> None:
        self.events.append(("lease", self.envelope.message_id))


def _worker(
    store: SQLiteExecutionStore,
    transport: _RecordingTransport,
    handler: Callable[[ExecutionRequest, Any], Awaitable[RunnerOutput]],
) -> ExecutionWorker:
    capability_runner = RegisteredCapabilityRunner({ROBOT_TEST_CAPABILITY: handler})
    unused = _UnusedRunner()
    return ExecutionWorker(
        settings=WorkerSettings(
            worker_id="abb-runner",
            node_id="robot-node",
            lease_seconds=10,
            lease_renewal_seconds=2,
        ),
        transport=transport,  # type: ignore[arg-type]
        store=store,
        dispatcher=ExecutionDispatcher(
            codex=unused,
            wake=unused,
            commands=unused,
            capabilities=capability_runner,
        ),
    )


async def test_abb_capability_redelivery_after_runner_restart_is_side_effect_idempotent(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def run_abb(request: ExecutionRequest, _cancellation: Any) -> RunnerOutput:
        calls.append(request.execution_id)
        return RunnerOutput(summary="ABB simulator E2E passed", output={"success": True})

    requester = EndpointRef(kind=EndpointKind.CONVERSATION, id="development-conversation")
    target = EndpointRef(kind=EndpointKind.CAPABILITY, id=ROBOT_TEST_CAPABILITY)
    request = ExecutionRequest(
        execution_id="abb-e2e-execution",
        operation=ExecutionOperation.INVOKE_ADAPTER,
        instruction="Run the ABB simulator server/client E2E suite",
        target=target,
        adapter=ROBOT_TEST_CAPABILITY,
        requested_by=requester,
        work_id="arci-v2-pr17",
    )
    envelope = BridgeEnvelope(
        message_id="abb-e2e-request",
        kind=MessageKind.REQUEST,
        sender=requester,
        destination=target,
        reply_to=requester,
        work_id=request.work_id,
        body=request.model_dump(mode="json"),
    )
    assert subject_for(envelope) == capability_subject(ROBOT_TEST_CAPABILITY)

    store_path = tmp_path / "abb-runner.sqlite3"
    events: list[tuple[str, str]] = []
    first_store = SQLiteExecutionStore(store_path)
    first_transport = _RecordingTransport(events)
    first_delivery = _Delivery(envelope, events, delivery_count=1)
    await _worker(first_store, first_transport, run_abb).process(first_delivery)  # type: ignore[arg-type]
    first_store.close()

    assert calls == [request.execution_id]
    assert events.index(("publish", f"result-{request.execution_id}")) < events.index(
        ("ack", envelope.message_id)
    )
    first_result = first_transport.results[-1][0]
    assert first_result.destination == requester
    assert first_result.correlation_id == request.execution_id
    assert first_result.body["status"] == "succeeded"

    # Model a process restart followed by JetStream redelivery of the same request.
    # The durable outcome may be republished, but the external ABB side effect must
    # never execute for a second time.
    second_store = SQLiteExecutionStore(store_path)
    second_transport = _RecordingTransport(events)
    second_delivery = _Delivery(envelope, events, delivery_count=2)
    await _worker(second_store, second_transport, run_abb).process(second_delivery)  # type: ignore[arg-type]
    second_store.close()

    assert calls == [request.execution_id]
    assert second_delivery.settled is True
    assert second_transport.results[-1][0].body == first_result.body


def test_legacy_mailbox_and_polling_clients_are_removed_from_active_product() -> None:
    removed_paths = (
        "bridge_server.py",
        "windows_bridge.ps1",
        "wait_for_bridge_message.sh",
        "tests/test_bridge_server.py",
    )
    assert [path for path in removed_paths if (REPOSITORY_ROOT / path).exists()] == []

    runtime_roots = (
        REPOSITORY_ROOT / "src",
        REPOSITORY_ROOT / "scripts",
        REPOSITORY_ROOT / "deploy",
    )
    forbidden = (
        "/v1/messages",
        "wait_for_bridge_message.sh",
        "windows_bridge.ps1",
        "PARTIES = {\"ubuntu\", \"windows\"}",
        "AGENT_BRIDGE_TOKEN",
        "messages.jsonl",
    )
    violations: list[str] = []
    for root in runtime_roots:
        paths = [root] if root.is_file() else list(root.rglob("*"))
        for path in paths:
            if (
                not path.is_file()
                or "__pycache__" in path.parts
                or any(part.endswith(".egg-info") for part in path.parts)
            ):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for needle in forbidden:
                if needle in content:
                    violations.append(f"{path.relative_to(REPOSITORY_ROOT)}: {needle}")
    assert violations == []


def test_headless_worker_is_an_installed_entry_point() -> None:
    project = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts: dict[str, str] = project["project"]["scripts"]
    target = scripts["agent-bridge-runner"]
    module_name, attribute_name = target.split(":", 1)
    assert callable(getattr(importlib.import_module(module_name), attribute_name))
