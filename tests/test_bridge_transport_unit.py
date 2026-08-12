from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_bridge_bridge.codec import EnvelopeCodecError, decode_envelope, encode_envelope
from agent_bridge_bridge.idempotency import ClaimResult, InMemoryIdempotencyStore
from agent_bridge_bridge.subjects import (
    SubjectError,
    capability_subject,
    control_subject,
    dead_letter_subject,
    event_subject,
    inbox_subject,
    result_subject,
    room_subject,
    subject_for,
    validate_subject,
)
from agent_bridge_bridge.transport import JetStreamSettings
from agent_bridge_protocol.models import (
    BridgeEnvelope,
    DeliveryPolicy,
    EndpointKind,
    EndpointRef,
    MessageKind,
)


def envelope(*, destination: EndpointRef | None = None) -> BridgeEnvelope:
    return BridgeEnvelope(
        message_id="msg-1",
        kind=MessageKind.REQUEST,
        sender=EndpointRef(kind=EndpointKind.ROLE, id="role-source"),
        destination=destination or EndpointRef(kind=EndpointKind.NODE, id="node-a"),
        correlation_id="correlation-1",
        body={"instruction": "test"},
        delivery=DeliveryPolicy(max_attempts=2, retry_backoff_seconds=0),
    )


def test_subject_families_are_canonical_and_safe() -> None:
    node = EndpointRef(kind=EndpointKind.NODE, id="node-a")
    assert inbox_subject(node) == "bridge.v1.inbox.node.node-a"
    assert capability_subject("robot-test") == "bridge.v1.capability.robot-test"
    assert room_subject("planning") == "bridge.v1.room.planning"
    assert result_subject("request-1") == "bridge.v1.result.request-1"
    assert control_subject(node) == "bridge.v1.control.node.node-a"
    assert event_subject("progress") == "bridge.v1.event.progress"
    assert dead_letter_subject(inbox_subject(node)) == "bridge.v1.dead.inbox"
    assert subject_for(envelope()) == inbox_subject(node)
    assert subject_for(
        envelope(destination=EndpointRef(kind=EndpointKind.CAPABILITY, id="robot-test"))
    ) == capability_subject("robot-test")
    assert validate_subject("bridge.v1.inbox.node.node-a") == "bridge.v1.inbox.node.node-a"


@pytest.mark.parametrize(
    "unsafe",
    [
        "node.*",
        "node.>",
        "two words",
        "bridge.v1.inbox.node.*",
        "bridge.v1.inbox.node.node.extra",
        "outside.v1.inbox.node",
    ],
)
def test_subject_wildcard_and_shape_injection_is_rejected(unsafe: str) -> None:
    with pytest.raises(SubjectError):
        if unsafe.startswith("bridge.") or unsafe.startswith("outside."):
            validate_subject(unsafe)
        else:
            inbox_subject(EndpointRef(kind=EndpointKind.NODE, id=unsafe))


def test_versioned_envelope_codec_round_trip_and_rejects_unknown_schema() -> None:
    original = envelope()
    decoded = decode_envelope(encode_envelope(original))
    assert decoded == original

    with pytest.raises(EnvelopeCodecError):
        decode_envelope(b'{"schema_version":"agent-bridge/v2"}')


async def test_idempotency_claim_lifecycle() -> None:
    store = InMemoryIdempotencyStore()
    assert await store.claim("msg-1", owner="runner-a", ttl_seconds=30) == ClaimResult.CLAIMED
    assert await store.claim("msg-1", owner="runner-b", ttl_seconds=30) == ClaimResult.IN_PROGRESS
    await store.complete("msg-1", owner="runner-a")
    assert await store.claim("msg-1", owner="runner-b", ttl_seconds=30) == ClaimResult.COMPLETED
    with pytest.raises(PermissionError):
        await store.release("msg-1", owner="runner-b")


def test_expiration_policy_remains_timezone_aware() -> None:
    expires_at = datetime.now(UTC) + timedelta(minutes=1)
    message = envelope()
    message.delivery.expires_at = expires_at
    assert decode_envelope(encode_envelope(message)).delivery.expires_at == expires_at


def test_nats_user_password_must_be_complete_and_exclusive() -> None:
    with pytest.raises(ValueError, match="configured together"):
        JetStreamSettings(username="node-a")
    with pytest.raises(ValueError, match="mutually exclusive"):
        JetStreamSettings(credentials_file=Path("node.creds"), username="node-a", password="secret")
