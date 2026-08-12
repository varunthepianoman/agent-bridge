"""Canonical and injection-safe Bridge subject names."""

from __future__ import annotations

import re

from agent_bridge_protocol.models import BridgeEnvelope, EndpointKind, EndpointRef

NAMESPACE = "bridge.v1"
WORK_STREAM = "BRIDGE_WORK_V1"
EVENTS_STREAM = "BRIDGE_EVENTS_V1"
DEAD_LETTER_STREAM = "BRIDGE_DLQ_V1"

WORK_SUBJECTS = (
    f"{NAMESPACE}.inbox.>",
    f"{NAMESPACE}.capability.>",
    f"{NAMESPACE}.room.>",
    f"{NAMESPACE}.result.>",
    f"{NAMESPACE}.control.>",
)
EVENT_SUBJECTS = (f"{NAMESPACE}.event.>",)
DEAD_LETTER_SUBJECTS = (f"{NAMESPACE}.dead.>",)

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_FAMILIES = frozenset({"inbox", "capability", "room", "result", "control", "event"})


class SubjectError(ValueError):
    """A routing identity cannot safely be placed in a NATS subject."""


def validate_token(value: str, *, label: str = "identity") -> str:
    """Reject wildcard, separator, whitespace, and unbounded subject tokens."""

    if not _TOKEN.fullmatch(value):
        raise SubjectError(
            f"{label} must match [A-Za-z0-9_-]{{1,128}}; "
            "NATS separators and wildcards are forbidden"
        )
    return value


def inbox_subject(destination: EndpointRef) -> str:
    return (
        f"{NAMESPACE}.inbox.{validate_token(str(destination.kind), label='endpoint kind')}."
        f"{validate_token(destination.id)}"
    )


def capability_subject(capability_id: str) -> str:
    return f"{NAMESPACE}.capability.{validate_token(capability_id, label='capability id')}"


def room_subject(room_id: str) -> str:
    return f"{NAMESPACE}.room.{validate_token(room_id, label='room id')}"


def result_subject(correlation_id: str) -> str:
    return f"{NAMESPACE}.result.{validate_token(correlation_id, label='correlation id')}"


def control_subject(destination: EndpointRef) -> str:
    return (
        f"{NAMESPACE}.control.{validate_token(str(destination.kind), label='endpoint kind')}."
        f"{validate_token(destination.id)}"
    )


def event_subject(topic: str) -> str:
    return f"{NAMESPACE}.event.{validate_token(topic, label='event topic')}"


def dead_letter_subject(original_subject: str) -> str:
    """Route a failed message to a DLQ partitioned by its original family."""

    parts = original_subject.split(".")
    if len(parts) < 4 or parts[:2] != ["bridge", "v1"] or parts[2] not in _FAMILIES:
        raise SubjectError(f"not a canonical Bridge subject: {original_subject}")
    return f"{NAMESPACE}.dead.{parts[2]}"


def validate_subject(subject: str) -> str:
    """Validate a fully materialized Bridge subject, including custom routes."""

    parts = subject.split(".")
    if len(parts) < 4 or parts[:2] != ["bridge", "v1"]:
        raise SubjectError(f"not a canonical Bridge subject: {subject}")
    family = parts[2]
    expected_parts = {
        "inbox": 5,
        "capability": 4,
        "room": 4,
        "result": 4,
        "control": 5,
        "event": 4,
        "dead": 4,
    }
    if family not in expected_parts or len(parts) != expected_parts[family]:
        raise SubjectError(f"invalid Bridge subject shape: {subject}")
    for index, part in enumerate(parts[3:], start=3):
        validate_token(part, label=f"subject token {index}")
    return subject


def subject_for(envelope: BridgeEnvelope) -> str:
    """Resolve normal delivery routing from an envelope destination."""

    destination = envelope.destination
    if destination.kind == EndpointKind.CAPABILITY:
        return capability_subject(destination.id)
    if destination.kind == EndpointKind.ROOM:
        return room_subject(destination.id)
    return inbox_subject(destination)
