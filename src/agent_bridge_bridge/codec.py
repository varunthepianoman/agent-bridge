"""Wire codec for versioned Bridge envelopes."""

from __future__ import annotations

from pydantic import ValidationError

from agent_bridge_protocol.models import BridgeEnvelope


class EnvelopeCodecError(ValueError):
    """Payload is not a valid current-version Bridge envelope."""


def encode_envelope(envelope: BridgeEnvelope) -> bytes:
    return envelope.model_dump_json().encode("utf-8")


def decode_envelope(payload: bytes) -> BridgeEnvelope:
    try:
        return BridgeEnvelope.model_validate_json(payload)
    except (ValidationError, UnicodeDecodeError, ValueError) as error:
        raise EnvelopeCodecError("invalid agent-bridge/v1 envelope") from error
