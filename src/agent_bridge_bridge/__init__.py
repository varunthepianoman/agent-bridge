"""Durable NATS JetStream transport for Agent Bridge."""

from .codec import EnvelopeCodecError, decode_envelope, encode_envelope
from .idempotency import ClaimResult, IdempotencyStore, InMemoryIdempotencyStore
from .logging_context import bind_log_context, current_log_context, structured_extra
from .observer import BrokerActivity, BrokerActivityKind, TransportObserver
from .subjects import (
    DEAD_LETTER_STREAM,
    DEAD_LETTER_SUBJECTS,
    EVENT_SUBJECTS,
    EVENTS_STREAM,
    NAMESPACE,
    WORK_STREAM,
    WORK_SUBJECTS,
    SubjectError,
    dead_letter_subject,
    event_subject,
    inbox_subject,
    room_subject,
    subject_for,
    validate_subject,
)
from .transport import (
    BridgeDelivery,
    BridgeSubscription,
    JetStreamSettings,
    JetStreamTransport,
    PublishedMessage,
)

__all__ = [name for name in globals() if not name.startswith("_")]
