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
    assert envelope.delivery.strategy == "mailbox"
    with pytest.raises(ValidationError):
        BridgeEnvelope.model_validate({**envelope.model_dump(), "work_id": "removed"})


@pytest.mark.parametrize("strategy", ["mailbox", "queue", "steer-or-queue"])
def test_delivery_policy_accepts_mailbox_and_legacy_strategies(strategy: str) -> None:
    envelope = BridgeEnvelope(
        message_id=f"message-{strategy}",
        kind=MessageKind.MESSAGE,
        sender=EndpointRef(kind=EndpointKind.ENDPOINT, id="human"),
        destination=EndpointRef(kind=EndpointKind.CONVERSATION, id="conversation-1"),
        delivery={"strategy": strategy},
    )

    assert envelope.delivery.strategy == strategy
