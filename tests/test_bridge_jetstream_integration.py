from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_bridge_bridge.collaboration import CollaborationClient
from agent_bridge_bridge.execution_store import SQLiteExecutionStore
from agent_bridge_bridge.observer import BrokerActivity, BrokerActivityKind
from agent_bridge_bridge.runners import (
    ExecutionDispatcher,
    RetryableRunnerError,
    RunnerOutput,
)
from agent_bridge_bridge.subjects import control_subject, dead_letter_subject, inbox_subject
from agent_bridge_bridge.transport import JetStreamSettings, JetStreamTransport
from agent_bridge_bridge.worker import ControlWorker, ExecutionWorker, WorkerSettings
from agent_bridge_protocol.models import (
    BridgeEnvelope,
    DeliveryPolicy,
    EndpointKind,
    EndpointRef,
    ExecutionOperation,
    ExecutionRequest,
    MessageKind,
)


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


@dataclass(frozen=True)
class NatsServer:
    container_name: str

    @property
    def port(self) -> int:
        result = subprocess.run(
            ["docker", "port", self.container_name, "4222/tcp"],
            check=True,
            capture_output=True,
            text=True,
        )
        return int(result.stdout.strip().rsplit(":", 1)[1])

    @property
    def url(self) -> str:
        return f"nats://127.0.0.1:{self.port}"


@dataclass
class RecordingObserver:
    activities: list[BrokerActivity]

    async def record(self, activity: BrokerActivity) -> None:
        self.activities.append(activity)


def _wait_for_port(port: int) -> None:
    for _ in range(100):
        with socket.socket() as probe:
            probe.settimeout(0.1)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    pytest.fail("temporary NATS server did not start")


@pytest.fixture(scope="module")
def nats_server() -> Iterator[NatsServer]:
    if not _docker_available():
        pytest.skip("Docker daemon is unavailable")
    name = f"agent-bridge-nats-test-{uuid.uuid4().hex[:10]}"
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                name,
                "--publish",
                "127.0.0.1::4222",
                "nats:2.11-alpine",
                "--jetstream",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        port_result = subprocess.run(
            ["docker", "port", name, "4222/tcp"],
            check=True,
            capture_output=True,
            text=True,
        )
        port = int(port_result.stdout.strip().rsplit(":", 1)[1])
        _wait_for_port(port)
        yield NatsServer(container_name=name)
    finally:
        subprocess.run(
            ["docker", "rm", "--force", name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def make_envelope(message_id: str, *, max_attempts: int = 2) -> BridgeEnvelope:
    return BridgeEnvelope(
        message_id=message_id,
        kind=MessageKind.REQUEST,
        sender=EndpointRef(kind=EndpointKind.ROLE, id="role-a"),
        destination=EndpointRef(kind=EndpointKind.NODE, id="node-a"),
        correlation_id=f"correlation-{message_id}",
        body={"instruction": "run"},
        delivery=DeliveryPolicy(
            max_attempts=max_attempts,
            retry_backoff_seconds=0.01,
            acknowledgement_timeout_seconds=0.1,
        ),
    )


async def test_offline_delivery_deduplication_redelivery_and_dead_letter(
    nats_server: NatsServer,
) -> None:
    settings = JetStreamSettings(servers=(nats_server.url,), duplicate_window_seconds=60)
    observer = RecordingObserver(activities=[])
    original = make_envelope(f"msg-{uuid.uuid4().hex}")
    subject = inbox_subject(original.destination)

    async with JetStreamTransport(settings, observer=observer) as sender:
        await sender.provision_streams()
        first = await sender.publish(original)
        duplicate = await sender.publish(original)
        assert first.stream == "BRIDGE_WORK_V1"
        assert duplicate.duplicate is True

    # Both the sender and broker restart before the receiver connects.
    subprocess.run(
        ["docker", "restart", nats_server.container_name],
        check=True,
        capture_output=True,
        timeout=30,
    )
    _wait_for_port(nats_server.port)

    # JetStream file storage remains authoritative across both restarts.
    receiver_settings = JetStreamSettings(servers=(nats_server.url,), duplicate_window_seconds=60)
    async with JetStreamTransport(receiver_settings, observer=observer) as receiver:
        subscription = await receiver.subscribe(
            subject,
            durable_name=f"runner-{uuid.uuid4().hex}",
            ack_wait_seconds=0.1,
        )
        deliveries = await subscription.fetch(timeout=2)
        assert len(deliveries) == 1
        assert deliveries[0].envelope == original
        assert deliveries[0].delivery_count == 1
        await deliveries[0].nak(reason="runner_failed")

        retried = await subscription.fetch(timeout=2)
        assert len(retried) == 1
        assert retried[0].delivery_count == 2
        dead_ack = await retried[0].nak(reason="attempts_exhausted")
        assert dead_ack is None

        dead_subscription = await receiver.subscribe(
            dead_letter_subject(subject),
            durable_name=f"dead-reader-{uuid.uuid4().hex}",
        )
        dead = await dead_subscription.fetch(timeout=2)
        assert len(dead) == 1
        assert dead[0].payload == original.model_dump_json().encode()
        assert dead[0].headers["X-Bridge-Dead-Reason"] == "attempts_exhausted"
        await dead[0].ack()

    kinds = [activity.kind for activity in observer.activities]
    assert kinds.count(BrokerActivityKind.PUBLISHED) == 2
    assert BrokerActivityKind.DELIVERED in kinds
    assert BrokerActivityKind.RETRY_SCHEDULED in kinds
    assert BrokerActivityKind.DEAD_LETTERED in kinds
    assert BrokerActivityKind.ACKNOWLEDGED in kinds
    published = next(
        activity
        for activity in observer.activities
        if activity.kind == BrokerActivityKind.PUBLISHED
    )
    assert published.detail["destination_id"] == "node-a"
    assert published.detail["encoded_size"] > 0
    assert "instruction" not in published.detail
    delivered = next(
        activity
        for activity in observer.activities
        if activity.kind == BrokerActivityKind.DELIVERED
    )
    assert delivered.consumer is not None
    assert delivered.consumer_sequence == 1


class InstantRunner:
    async def run(self, _request: Any, _cancellation: Any, progress: Any) -> RunnerOutput:
        await progress("working", 50)
        return RunnerOutput(summary="runner completed", output={"value": 42})


class SlowRunner(InstantRunner):
    async def run(self, request: Any, cancellation: Any, progress: Any) -> RunnerOutput:
        await progress("working", 25)
        await asyncio.sleep(0.2)
        await cancellation.raise_if_cancelled()
        return RunnerOutput(summary="slow runner completed", output={"value": 42})


class FlakyRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, _request: Any, _cancellation: Any, _progress: Any) -> RunnerOutput:
        self.calls += 1
        if self.calls == 1:
            raise RetryableRunnerError("temporary test failure")
        return RunnerOutput(summary="recovered")


async def test_worker_publishes_result_before_acknowledging_input(
    nats_server: NatsServer, tmp_path: Path
) -> None:
    observer = RecordingObserver(activities=[])
    settings = JetStreamSettings(servers=(nats_server.url,))
    target = EndpointRef(kind=EndpointKind.NODE, id="node-worker")
    execution = ExecutionRequest(
        execution_id="exec-worker-1",
        operation=ExecutionOperation.NEW_EXECUTION,
        instruction="perform work",
        target=target,
    )
    envelope = BridgeEnvelope(
        message_id="msg-worker-1",
        kind=MessageKind.REQUEST,
        sender=EndpointRef(kind=EndpointKind.ROLE, id="role-requester"),
        destination=target,
        body=execution.model_dump(mode="json"),
    )
    store = SQLiteExecutionStore(tmp_path / "worker.sqlite3")
    runner = SlowRunner()
    dispatcher = ExecutionDispatcher(
        codex=runner,
        wake=runner,
        commands=runner,
        capabilities=runner,
    )

    async with JetStreamTransport(settings, observer=observer) as transport:
        await transport.provision_streams()
        inputs = await transport.subscribe(
            inbox_subject(target),
            durable_name=f"worker-{uuid.uuid4().hex}",
            ack_wait_seconds=0.1,
        )
        results = await transport.subscribe(
            "bridge.v1.result.exec-worker-1",
            durable_name=f"result-reader-{uuid.uuid4().hex}",
        )
        await transport.publish(envelope)
        worker = ExecutionWorker(
            settings=WorkerSettings(
                worker_id="worker-a",
                node_id="node-worker",
                lease_seconds=0.2,
                lease_renewal_seconds=0.05,
            ),
            transport=transport,
            store=store,
            dispatcher=dispatcher,
        )
        assert await worker.run_once(inputs, timeout=2)
        delivered_results = await results.fetch(batch=2, timeout=2)
        outcome = next(
            delivery
            for delivery in delivered_results
            if delivery.envelope.message_id == "result-exec-worker-1"
        )
        assert outcome.envelope.body["status"] == "succeeded"
        for delivery in delivered_results:
            await delivery.ack()

    published_index = next(
        index
        for index, activity in enumerate(observer.activities)
        if activity.kind == BrokerActivityKind.PUBLISHED
        and activity.message_id == "result-exec-worker-1"
    )
    input_ack_index = next(
        index
        for index, activity in enumerate(observer.activities)
        if activity.kind == BrokerActivityKind.ACKNOWLEDGED
        and activity.message_id == "msg-worker-1"
    )
    assert published_index < input_ack_index
    assert BrokerActivityKind.LEASE_EXTENDED in [activity.kind for activity in observer.activities]
    persisted = await store.outcome("exec-worker-1")
    assert persisted is not None
    assert persisted.status == "succeeded"
    store.close()


async def test_worker_retries_with_a_new_attempt_then_completes(
    nats_server: NatsServer, tmp_path: Path
) -> None:
    settings = JetStreamSettings(servers=(nats_server.url,))
    target = EndpointRef(kind=EndpointKind.NODE, id="node-flaky")
    request = ExecutionRequest(
        execution_id="exec-flaky-1",
        operation=ExecutionOperation.NEW_EXECUTION,
        instruction="retry this",
        target=target,
    )
    envelope = BridgeEnvelope(
        message_id="msg-flaky-1",
        kind=MessageKind.REQUEST,
        sender=EndpointRef(kind=EndpointKind.ROLE, id="role-requester"),
        destination=target,
        body=request.model_dump(mode="json"),
        delivery=DeliveryPolicy(max_attempts=2, retry_backoff_seconds=0.01),
    )
    store = SQLiteExecutionStore(tmp_path / "flaky.sqlite3")
    runner = FlakyRunner()
    dispatcher = ExecutionDispatcher(
        codex=runner, wake=runner, commands=runner, capabilities=runner
    )
    async with JetStreamTransport(settings) as transport:
        await transport.provision_streams()
        inputs = await transport.subscribe(
            inbox_subject(target), durable_name=f"flaky-{uuid.uuid4().hex}"
        )
        await transport.publish(envelope)
        worker = ExecutionWorker(
            settings=WorkerSettings(
                worker_id="worker-flaky",
                node_id="node-flaky",
                lease_seconds=2,
                lease_renewal_seconds=0.5,
            ),
            transport=transport,
            store=store,
            dispatcher=dispatcher,
        )
        assert await worker.run_once(inputs, timeout=2)
        assert await worker.run_once(inputs, timeout=2)

    attempts = await store.attempts("exec-flaky-1")
    assert [attempt.status for attempt in attempts] == ["failed", "succeeded"]
    assert runner.calls == 2
    store.close()


async def test_control_message_cancels_the_next_execution_end_to_end(
    nats_server: NatsServer, tmp_path: Path
) -> None:
    settings = JetStreamSettings(servers=(nats_server.url,))
    target = EndpointRef(kind=EndpointKind.NODE, id="node-cancel")
    control_envelope = BridgeEnvelope(
        message_id="control-cancel-1",
        kind=MessageKind.CONTROL,
        sender=EndpointRef(kind=EndpointKind.ROLE, id="role-user"),
        destination=target,
        body={"operation": "cancel", "execution_id": "exec-cancel-1", "reason": "stop"},
    )
    request = ExecutionRequest(
        execution_id="exec-cancel-1",
        operation=ExecutionOperation.NEW_EXECUTION,
        instruction="should not run",
        target=target,
    )
    input_envelope = BridgeEnvelope(
        message_id="msg-cancel-1",
        kind=MessageKind.REQUEST,
        sender=EndpointRef(kind=EndpointKind.ROLE, id="role-user"),
        destination=target,
        body=request.model_dump(mode="json"),
    )
    store = SQLiteExecutionStore(tmp_path / "cancel-e2e.sqlite3")
    runner = InstantRunner()
    dispatcher = ExecutionDispatcher(
        codex=runner, wake=runner, commands=runner, capabilities=runner
    )
    async with JetStreamTransport(settings) as transport:
        await transport.provision_streams()
        controls = await transport.subscribe(
            control_subject(target), durable_name=f"control-{uuid.uuid4().hex}"
        )
        inputs = await transport.subscribe(
            inbox_subject(target), durable_name=f"cancel-input-{uuid.uuid4().hex}"
        )
        results = await transport.subscribe(
            "bridge.v1.result.exec-cancel-1",
            durable_name=f"cancel-result-{uuid.uuid4().hex}",
        )
        await transport.publish(control_envelope, subject=control_subject(target))
        assert await ControlWorker(store).run_once(controls, timeout=2)
        await transport.publish(input_envelope)
        worker = ExecutionWorker(
            settings=WorkerSettings(
                worker_id="worker-cancel",
                node_id="node-cancel",
                lease_seconds=2,
                lease_renewal_seconds=0.5,
            ),
            transport=transport,
            store=store,
            dispatcher=dispatcher,
        )
        assert await worker.run_once(inputs, timeout=2)
        outcome = (await results.fetch(timeout=2))[0]
        assert outcome.envelope.body["status"] == "cancelled"
        await outcome.ack()
    store.close()


async def test_expired_execution_publishes_failure_then_dead_letters_input(
    nats_server: NatsServer, tmp_path: Path
) -> None:
    settings = JetStreamSettings(servers=(nats_server.url,))
    target = EndpointRef(kind=EndpointKind.NODE, id="node-expiry")
    request = ExecutionRequest(
        execution_id="exec-expiry-1",
        operation=ExecutionOperation.NEW_EXECUTION,
        instruction="too late",
        target=target,
    )
    envelope = BridgeEnvelope(
        message_id="msg-expiry-1",
        kind=MessageKind.REQUEST,
        sender=EndpointRef(kind=EndpointKind.ROLE, id="role-user"),
        destination=target,
        body=request.model_dump(mode="json"),
        delivery=DeliveryPolicy(expires_at=datetime.now(UTC) + timedelta(milliseconds=50)),
    )
    store = SQLiteExecutionStore(tmp_path / "expiry.sqlite3")
    runner = InstantRunner()
    dispatcher = ExecutionDispatcher(
        codex=runner, wake=runner, commands=runner, capabilities=runner
    )
    async with JetStreamTransport(settings) as transport:
        await transport.provision_streams()
        inputs = await transport.subscribe(
            inbox_subject(target), durable_name=f"expiry-input-{uuid.uuid4().hex}"
        )
        results = await transport.subscribe(
            "bridge.v1.result.exec-expiry-1",
            durable_name=f"expiry-result-{uuid.uuid4().hex}",
        )
        dead = await transport.subscribe(
            dead_letter_subject(inbox_subject(target)),
            durable_name=f"expiry-dead-{uuid.uuid4().hex}",
        )
        await transport.publish(envelope)
        await asyncio.sleep(0.1)
        worker = ExecutionWorker(
            settings=WorkerSettings(
                worker_id="worker-expiry",
                node_id="node-expiry",
                lease_seconds=2,
                lease_renewal_seconds=0.5,
            ),
            transport=transport,
            store=store,
            dispatcher=dispatcher,
        )
        assert await worker.run_once(inputs, timeout=2)
        outcome = (await results.fetch(timeout=2))[0]
        assert outcome.envelope.body["status"] == "expired"
        dead_input = None
        for _ in range(10):
            candidate = (await dead.fetch(timeout=2))[0]
            if candidate.envelope.message_id == "msg-expiry-1":
                dead_input = candidate
                break
            await candidate.ack()
        assert dead_input is not None
        assert dead_input.headers["X-Bridge-Dead-Reason"] == "expired"
        await outcome.ack()
        await dead_input.ack()
    store.close()


async def test_durable_collaboration_request_reply_survives_disconnected_requester(
    nats_server: NatsServer,
) -> None:
    settings = JetStreamSettings(servers=(nats_server.url,))
    suffix = uuid.uuid4().hex
    requester_id = EndpointRef(kind=EndpointKind.ENDPOINT, id=f"controller-{suffix}")
    responder_id = EndpointRef(kind=EndpointKind.ROLE, id=f"worker-{suffix}")

    async with JetStreamTransport(settings) as first_transport:
        await first_transport.provision_streams()
        requester = CollaborationClient(first_transport, identity=requester_id)
        responder = CollaborationClient(first_transport, identity=responder_id)
        requests = await responder.subscribe_inbox(consumer_id="primary")
        pending = await requester.request(
            responder_id,
            {"objective": "audit the plan"},
            work_id="work-primary",
            extensions={
                "alternate.controller": {"name": "aiwk"},
                "cross_work": ["work-secondary"],
            },
        )
        delivered_request = (await requests.fetch(timeout=2))[0]
        assert delivered_request.envelope.extensions["alternate.controller"] == {"name": "aiwk"}
        await delivered_request.ack()

    # The requester is disconnected while the response is durably published.
    async with JetStreamTransport(settings) as response_transport:
        responder = CollaborationClient(response_transport, identity=responder_id)
        response = await responder.reply(
            delivered_request.envelope,
            {"decision": "accepted"},
            kind=MessageKind.ACCEPTANCE,
            extensions={"unknown.future.extension": {"version": 9}},
        )
        assert response.envelope.causation_id == pending.envelope.message_id

    async with JetStreamTransport(settings) as resumed_transport:
        requester = CollaborationClient(resumed_transport, identity=requester_id)
        replies = await requester.subscribe_replies(pending, subscriber_id="ui")
        reply = (await replies.fetch(timeout=2))[0]
        assert reply.envelope.correlation_id == pending.envelope.correlation_id
        assert reply.envelope.body == {"decision": "accepted"}
        assert reply.envelope.extensions["unknown.future.extension"] == {"version": 9}
        await reply.ack()


async def test_direct_peer_hierarchy_capability_fanout_room_and_event_topologies_coexist(
    nats_server: NatsServer,
) -> None:
    settings = JetStreamSettings(servers=(nats_server.url,))
    suffix = uuid.uuid4().hex
    parent_id = EndpointRef(kind=EndpointKind.ROLE, id=f"parent-{suffix}")
    child_id = EndpointRef(kind=EndpointKind.ROLE, id=f"child-{suffix}")
    peer_id = EndpointRef(kind=EndpointKind.ROLE, id=f"peer-{suffix}")
    controller_id = EndpointRef(kind=EndpointKind.ENDPOINT, id=f"policy-{suffix}")
    capability = f"review-{suffix}"
    room = f"planning-{suffix}"
    topic = f"status-{suffix}"

    async with JetStreamTransport(settings) as transport:
        await transport.provision_streams()
        parent = CollaborationClient(transport, identity=parent_id)
        child = CollaborationClient(transport, identity=child_id)
        peer = CollaborationClient(transport, identity=peer_id)
        controller = CollaborationClient(transport, identity=controller_id)
        child_inbox = await child.subscribe_inbox(consumer_id="agent")
        peer_inbox = await peer.subscribe_inbox(consumer_id="agent")
        controller_inbox = await controller.subscribe_inbox(consumer_id="policy")
        capability_workers = await child.subscribe_capability(capability, worker_group="reviewers")
        room_child = await child.subscribe_room(room, participant_id=child_id.id)
        room_peer = await peer.subscribe_room(room, participant_id=peer_id.id)
        event_child = await child.subscribe_events(topic, subscriber_id=child_id.id)
        event_controller = await controller.subscribe_events(topic, subscriber_id=controller_id.id)

        (
            hierarchy,
            peer_turn,
            fanout,
            capability_message,
            room_message,
            event_message,
        ) = await asyncio.gather(
            parent.send(child_id, {"report": "parent to child"}),
            child.send(
                peer_id,
                {"proposal": "peer review"},
                kind=MessageKind.PROPOSAL,
            ),
            parent.fan_out(
                [peer_id, controller_id],
                {"notice": "shared"},
                work_id="work-a",
            ),
            parent.dispatch_capability(capability, {"task": "audit"}),
            parent.publish_room(room, {"decision": "discuss"}),
            parent.publish_event(topic, {"status": "active"}),
        )
        assert hierarchy.envelope.destination == child_id
        assert peer_turn.envelope.kind == MessageKind.PROPOSAL
        assert len({item.envelope.message_id for item in fanout}) == 2
        assert len({item.envelope.correlation_id for item in fanout}) == 1
        assert {item.envelope.causation_id for item in fanout} == {None}
        assert (
            len({item.envelope.extensions["agent_bridge.fanout_group_id"] for item in fanout}) == 1
        )
        assert capability_message.acknowledgement.duplicate is False
        assert room_message.acknowledgement.stream == "BRIDGE_WORK_V1"
        assert event_message.acknowledgement.stream == "BRIDGE_EVENTS_V1"

        child_messages = await child_inbox.fetch(timeout=2)
        peer_messages = await peer_inbox.fetch(batch=2, timeout=2)
        controller_messages = await controller_inbox.fetch(timeout=2)
        capability_messages = await capability_workers.fetch(timeout=2)
        child_room_messages = await room_child.fetch(timeout=2)
        peer_room_messages = await room_peer.fetch(timeout=2)
        child_events = await event_child.fetch(timeout=2)
        controller_events = await event_controller.fetch(timeout=2)

        assert [item.envelope.body for item in child_messages] == [{"report": "parent to child"}]
        assert {item.envelope.kind for item in peer_messages} == {
            MessageKind.PROPOSAL,
            MessageKind.MESSAGE,
        }
        assert [item.envelope.body for item in controller_messages] == [{"notice": "shared"}]
        assert [item.envelope.body for item in capability_messages] == [{"task": "audit"}]
        assert [item.envelope.body for item in child_room_messages] == [{"decision": "discuss"}]
        assert [item.envelope.body for item in peer_room_messages] == [{"decision": "discuss"}]
        assert [item.envelope.body for item in child_events] == [{"status": "active"}]
        assert [item.envelope.body for item in controller_events] == [{"status": "active"}]
        for delivery in (
            child_messages
            + peer_messages
            + controller_messages
            + capability_messages
            + child_room_messages
            + peer_room_messages
            + child_events
            + controller_events
        ):
            await delivery.ack()


async def test_capability_group_redelivers_to_peer_and_room_participant_replays_offline(
    nats_server: NatsServer,
) -> None:
    settings = JetStreamSettings(servers=(nats_server.url,))
    suffix = uuid.uuid4().hex
    capability = f"shared-{suffix}"
    room = f"offline-{suffix}"
    sender_id = EndpointRef(kind=EndpointKind.ROLE, id=f"sender-{suffix}")
    worker_id = EndpointRef(kind=EndpointKind.NODE, id=f"worker-{suffix}")
    participant_id = f"participant-{suffix}"

    first_transport = JetStreamTransport(settings)
    second_transport = JetStreamTransport(settings)
    await first_transport.connect()
    await second_transport.connect()
    try:
        await first_transport.provision_streams()
        sender = CollaborationClient(first_transport, identity=sender_id)
        first_worker = CollaborationClient(first_transport, identity=worker_id)
        second_worker = CollaborationClient(second_transport, identity=worker_id)
        first_subscription = await first_worker.subscribe_capability(
            capability,
            worker_group="workers",
            ack_wait_seconds=0.1,
        )
        second_subscription = await second_worker.subscribe_capability(
            capability,
            worker_group="workers",
            ack_wait_seconds=0.1,
        )
        offline_room = await first_worker.subscribe_room(
            room,
            participant_id=participant_id,
        )
        del offline_room
        await sender.dispatch_capability(capability, {"job": "once"})
        first_delivery = (await first_subscription.fetch(timeout=2))[0]
        assert first_delivery.delivery_count == 1

        # Simulate the first process dying before ACK. The same durable group lets
        # the peer process claim the redelivery instead of executing a second copy.
        await first_transport.close()
        await asyncio.sleep(0.15)
        redelivered = (await second_subscription.fetch(timeout=2))[0]
        assert redelivered.envelope.message_id == first_delivery.envelope.message_id
        assert redelivered.delivery_count == 2
        await redelivered.ack()

        second_sender = CollaborationClient(second_transport, identity=sender_id)
        await second_sender.publish_room(room, {"offline": True})
    finally:
        await first_transport.close()
        await second_transport.close()

    async with JetStreamTransport(settings) as resumed_transport:
        participant = CollaborationClient(resumed_transport, identity=worker_id)
        resumed_room = await participant.subscribe_room(
            room,
            participant_id=participant_id,
        )
        replay = (await resumed_room.fetch(timeout=2))[0]
        assert replay.envelope.body == {"offline": True}
        await replay.ack()


async def test_live_broker_diagnostics_report_streams_consumer_lag_and_no_payloads(
    nats_server: NatsServer,
) -> None:
    settings = JetStreamSettings(servers=(nats_server.url,))
    suffix = uuid.uuid4().hex
    target = EndpointRef(kind=EndpointKind.ENDPOINT, id=f"diagnostic-{suffix}")
    envelope = BridgeEnvelope(
        message_id=f"diagnostic-message-{suffix}",
        kind=MessageKind.MESSAGE,
        sender=EndpointRef(kind=EndpointKind.ENDPOINT, id="diagnostic-sender"),
        destination=target,
        body={"secret": "must not appear in broker diagnostics"},
    )
    async with JetStreamTransport(settings) as transport:
        await transport.provision_streams()
        subscription = await transport.subscribe(
            inbox_subject(target),
            durable_name=f"diagnostic-consumer-{suffix}",
        )
        await transport.publish(envelope)
        diagnostics = await transport.diagnostics()
        assert diagnostics["connected"] is True
        assert {stream["name"] for stream in diagnostics["streams"]} == {
            "BRIDGE_WORK_V1",
            "BRIDGE_EVENTS_V1",
            "BRIDGE_DLQ_V1",
        }
        consumer = next(
            item
            for item in diagnostics["consumers"]
            if item["consumer"] == f"diagnostic-consumer-{suffix}"
        )
        assert consumer["pending_count"] == 1
        assert consumer["ack_pending_count"] == 0
        assert consumer["stale"] is False
        advisory = next(
            item
            for item in diagnostics["advisories"]
            if item.get("consumer") == consumer["consumer"]
        )
        assert advisory["code"] == "consumer_lag"
        assert "secret" not in str(diagnostics)
        delivery = (await subscription.fetch(timeout=2))[0]
        await delivery.ack()
