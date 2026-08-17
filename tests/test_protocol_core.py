from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_bridge_protocol import BridgeEnvelope, EndpointKind, EndpointRef, MessageKind


def test_minimal_envelope_is_strict_and_conversation_addressed() -> None:
    envelope = BridgeEnvelope(
        message_id="message-1",
        kind=MessageKind.MESSAGE,
        sender=EndpointRef(kind=EndpointKind.ENDPOINT, id="human"),
        destination=EndpointRef(kind=EndpointKind.CONVERSATION, id="conversation-1"),
        body={"text": "hello"},
    )
    assert envelope.schema_version == "agent-bridge/v1"
    with pytest.raises(ValidationError):
        BridgeEnvelope.model_validate({**envelope.model_dump(), "work_id": "removed"})
