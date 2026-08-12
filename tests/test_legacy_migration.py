from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_bridge_bridge.subjects import subject_for
from agent_bridge_protocol.models import (
    BridgeEnvelope,
    EndpointKind,
    EndpointRef,
    ExecutionRequest,
    MessageKind,
)

ROOT = Path(__file__).parents[1]


def test_abb_request_fixture_is_capability_addressed() -> None:
    fixture = json.loads(
        (ROOT / "examples" / "abb-robot-simulator-e2e.request.json").read_text(encoding="utf-8")
    )
    request = ExecutionRequest(
        execution_id="exec-abb-acceptance",
        requested_at=datetime.now(UTC),
        requested_by=EndpointRef(kind=EndpointKind.ENDPOINT, id="catalog-user"),
        **fixture["request"],
    )
    envelope = BridgeEnvelope(
        message_id="msg-abb-acceptance",
        kind=MessageKind.REQUEST,
        sender=EndpointRef(kind=EndpointKind.ENDPOINT, id="catalog-user"),
        destination=request.target,
        body=request.model_dump(mode="json"),
        work_id=request.work_id,
        delivery=request.delivery,
        extensions=fixture["envelope"]["extensions"],
    )

    assert request.adapter == "robot-simulator-e2e"
    assert request.target == EndpointRef(
        kind=EndpointKind.CAPABILITY, id="robot-simulator-e2e"
    )
    assert subject_for(envelope) == "bridge.v1.capability.robot-simulator-e2e"


def test_retired_mailbox_surface_is_absent() -> None:
    for relative_path in (
        "bridge_server.py",
        "windows_bridge.ps1",
        "wait_for_bridge_message.sh",
        "tests/test_bridge_server.py",
    ):
        assert not (ROOT / relative_path).exists()
