"""NATS JetStream delivery transport and explicit acknowledgement API."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

import nats
from nats.aio.client import Client as NATS
from nats.aio.msg import Msg
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import NotFoundError

from agent_bridge_protocol.models import BridgeEnvelope

from .codec import decode_envelope, encode_envelope
from .observer import BrokerActivity, BrokerActivityKind, TransportObserver
from .subjects import (
    DEAD_LETTER_STREAM,
    DEAD_LETTER_SUBJECTS,
    EVENT_SUBJECTS,
    EVENTS_STREAM,
    WORK_STREAM,
    WORK_SUBJECTS,
    dead_letter_subject,
    subject_for,
    validate_subject,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class JetStreamSettings:
    servers: tuple[str, ...] = ("nats://127.0.0.1:4222",)
    client_name: str = "agent-bridge"
    credentials_file: Path | None = None
    username: str | None = None
    password: str | None = None
    connect_timeout_seconds: float = 5.0
    stream_max_age_seconds: float = 30 * 24 * 60 * 60
    event_max_age_seconds: float = 7 * 24 * 60 * 60
    dead_letter_max_age_seconds: float = 90 * 24 * 60 * 60
    duplicate_window_seconds: float = 120.0
    replicas: int = 1

    def __post_init__(self) -> None:
        if (self.username is None) != (self.password is None):
            raise ValueError("NATS username and password must be configured together")
        if self.credentials_file is not None and self.username is not None:
            raise ValueError("NATS credentials file and username/password are mutually exclusive")


@dataclass(frozen=True)
class PublishedMessage:
    stream: str
    sequence: int
    duplicate: bool


class JetStreamTransport:
    """Owns a NATS connection and the three Bridge v1 JetStream streams."""

    def __init__(
        self,
        settings: JetStreamSettings | None = None,
        *,
        observer: TransportObserver | None = None,
    ) -> None:
        self.settings = settings or JetStreamSettings()
        self.observer = observer
        self._connection: NATS | None = None
        self._jetstream: JetStreamContext | None = None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *_error: object) -> None:
        await self.close()

    @property
    def connected(self) -> bool:
        return self._connection is not None and self._connection.is_connected

    @property
    def jetstream(self) -> JetStreamContext:
        if self._jetstream is None:
            raise RuntimeError("JetStream transport is not connected")
        return self._jetstream

    async def connect(self) -> None:
        if self.connected:
            return
        options: dict[str, Any] = {
            "servers": list(self.settings.servers),
            "name": self.settings.client_name,
            "connect_timeout": self.settings.connect_timeout_seconds,
        }
        if self.settings.credentials_file is not None:
            options["user_credentials"] = str(self.settings.credentials_file)
        if self.settings.username is not None:
            options["user"] = self.settings.username
        if self.settings.password is not None:
            options["password"] = self.settings.password
        self._connection = await nats.connect(**options)
        self._jetstream = self._connection.jetstream()

    async def close(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
        self._connection = None
        self._jetstream = None

    async def provision_streams(self) -> None:
        """Create or reconcile the Bridge-owned streams without touching consumers."""

        configurations = (
            self._stream_config(WORK_STREAM, WORK_SUBJECTS, self.settings.stream_max_age_seconds),
            self._stream_config(EVENTS_STREAM, EVENT_SUBJECTS, self.settings.event_max_age_seconds),
            self._stream_config(
                DEAD_LETTER_STREAM,
                DEAD_LETTER_SUBJECTS,
                self.settings.dead_letter_max_age_seconds,
            ),
        )
        for config in configurations:
            try:
                await self.jetstream.stream_info(config.name or "")
            except NotFoundError:
                await self.jetstream.add_stream(config=config)
            else:
                await self.jetstream.update_stream(config=config)

    async def diagnostics(self) -> dict[str, Any]:
        """Return selected live JetStream state without exposing credentials or payloads."""

        observed_at = datetime.now(UTC).isoformat()
        if not self.connected:
            return {
                "status": "unavailable",
                "connected": False,
                "observed_at": observed_at,
                "streams": [],
                "consumers": [],
                "advisories": [
                    {
                        "severity": "error",
                        "code": "broker_disconnected",
                        "message": "NATS is disconnected",
                    }
                ],
            }
        advisories: list[dict[str, Any]] = []
        try:
            account = await self.jetstream.account_info()
        except Exception as error:
            return {
                "status": "unavailable",
                "connected": True,
                "observed_at": observed_at,
                "streams": [],
                "consumers": [],
                "advisories": [
                    {
                        "severity": "error",
                        "code": "jetstream_unavailable",
                        "message": str(error),
                    }
                ],
            }
        streams: list[dict[str, Any]] = []
        consumers: list[dict[str, Any]] = []
        for stream_name in (WORK_STREAM, EVENTS_STREAM, DEAD_LETTER_STREAM):
            try:
                info = await self.jetstream.stream_info(stream_name)
            except NotFoundError:
                advisories.append(
                    {
                        "severity": "error",
                        "code": "stream_missing",
                        "stream": stream_name,
                        "message": f"required stream {stream_name} is missing",
                    }
                )
                continue
            state = info.state
            lost_messages = len(state.lost.msgs) if state.lost and state.lost.msgs else 0
            streams.append(
                {
                    "name": stream_name,
                    "messages": state.messages,
                    "bytes": state.bytes,
                    "first_sequence": state.first_seq,
                    "last_sequence": state.last_seq,
                    "consumer_count": state.consumer_count,
                    "deleted_count": state.num_deleted or 0,
                    "lost_messages": lost_messages,
                }
            )
            if lost_messages:
                advisories.append(
                    {
                        "severity": "error",
                        "code": "stream_data_lost",
                        "stream": stream_name,
                        "count": lost_messages,
                        "message": f"{stream_name} reports lost messages",
                    }
                )
            for consumer in await self.jetstream.consumers_info(stream_name):
                pending = consumer.num_pending or 0
                ack_pending = consumer.num_ack_pending or 0
                redelivered = consumer.num_redelivered or 0
                item = {
                    "stream": stream_name,
                    "consumer": consumer.name,
                    "pending_count": pending,
                    "ack_pending_count": ack_pending,
                    "redelivered_count": redelivered,
                    "waiting_count": consumer.num_waiting or 0,
                    "delivered_stream_sequence": (
                        consumer.delivered.stream_seq if consumer.delivered else 0
                    ),
                    "ack_floor_stream_sequence": (
                        consumer.ack_floor.stream_seq if consumer.ack_floor else 0
                    ),
                    "paused": bool(consumer.paused),
                    "stale": False,
                    "observed_at": observed_at,
                }
                consumers.append(item)
                if pending or ack_pending:
                    advisories.append(
                        {
                            "severity": "info",
                            "code": "consumer_lag",
                            "stream": stream_name,
                            "consumer": consumer.name,
                            "pending": pending,
                            "ack_pending": ack_pending,
                            "message": "consumer has pending work",
                        }
                    )
                if redelivered:
                    advisories.append(
                        {
                            "severity": "warning",
                            "code": "consumer_redelivery",
                            "stream": stream_name,
                            "consumer": consumer.name,
                            "count": redelivered,
                            "message": "consumer has redelivered messages",
                        }
                    )
        api_errors = account.api.errors
        if api_errors:
            advisories.append(
                {
                    "severity": "warning",
                    "code": "jetstream_api_errors",
                    "count": api_errors,
                    "message": "JetStream reports API errors",
                }
            )
        degraded = any(item.get("severity") in {"warning", "error"} for item in advisories)
        return {
            "status": "degraded" if degraded else "healthy",
            "connected": True,
            "observed_at": observed_at,
            "account": {
                "memory_bytes": account.memory,
                "storage_bytes": account.storage,
                "streams": account.streams,
                "consumers": account.consumers,
                "api_calls": account.api.total,
                "api_errors": api_errors,
            },
            "streams": streams,
            "consumers": consumers,
            "advisories": advisories,
        }

    def _stream_config(
        self, name: str, subjects: tuple[str, ...], max_age_seconds: float
    ) -> StreamConfig:
        return StreamConfig(
            name=name,
            subjects=list(subjects),
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            max_age=max_age_seconds,
            duplicate_window=self.settings.duplicate_window_seconds,
            num_replicas=self.settings.replicas,
            allow_direct=True,
        )

    async def publish(
        self,
        envelope: BridgeEnvelope,
        *,
        subject: str | None = None,
    ) -> PublishedMessage:
        """Durably publish, using message identity for broker-side de-duplication."""

        if envelope.delivery.expires_at is not None:
            expires_at = envelope.delivery.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                raise ValueError("cannot publish an expired Bridge envelope")
        destination = validate_subject(subject or subject_for(envelope))
        headers: dict[str, str] = {
            "Nats-Msg-Id": envelope.message_id,
            "X-Bridge-Schema": envelope.schema_version,
            "X-Bridge-Message-Id": envelope.message_id,
        }
        if envelope.correlation_id is not None:
            headers["X-Bridge-Correlation-Id"] = envelope.correlation_id
        payload = encode_envelope(envelope)
        acknowledgement = await self.jetstream.publish(
            destination,
            payload,
            headers=headers,
        )
        await self._observe(
            BrokerActivity(
                kind=BrokerActivityKind.PUBLISHED,
                subject=destination,
                message_id=envelope.message_id,
                correlation_id=envelope.correlation_id,
                stream=acknowledgement.stream,
                stream_sequence=acknowledgement.seq,
                detail={
                    "duplicate": bool(acknowledgement.duplicate),
                    "message_type": str(envelope.kind),
                    "source_kind": str(envelope.sender.kind),
                    "source_id": envelope.sender.id,
                    "destination_kind": str(envelope.destination.kind),
                    "destination_id": envelope.destination.id,
                    "work_id": envelope.work_id,
                    "expires_at": (
                        envelope.delivery.expires_at.isoformat()
                        if envelope.delivery.expires_at is not None
                        else None
                    ),
                    "encoded_size": len(payload),
                },
            )
        )
        return PublishedMessage(
            stream=acknowledgement.stream,
            sequence=acknowledgement.seq,
            duplicate=bool(acknowledgement.duplicate),
        )

    async def subscribe(
        self,
        subject: str,
        *,
        durable_name: str,
        ack_wait_seconds: float = 60.0,
        server_max_deliver: int = 100,
    ) -> BridgeSubscription:
        """Create a durable pull consumer; envelope policy controls final DLQ routing."""

        if ack_wait_seconds <= 0 or server_max_deliver < 1:
            raise ValueError("ack wait must be positive and max deliver must be at least one")
        subscription = await self.jetstream.pull_subscribe(
            subject,
            durable=durable_name,
            stream=_stream_for_subject(subject),
            config=ConsumerConfig(
                durable_name=durable_name,
                ack_policy=AckPolicy.EXPLICIT,
                ack_wait=ack_wait_seconds,
                max_deliver=server_max_deliver,
                filter_subject=subject,
            ),
        )
        return BridgeSubscription(self, subscription)

    async def _observe(self, activity: BrokerActivity) -> None:
        if self.observer is None:
            return
        try:
            await self.observer.record(activity)
        except Exception:
            LOGGER.exception(
                "Bridge transport observer failed", extra={"subject": activity.subject}
            )


def _stream_for_subject(subject: str) -> str:
    """Select the owned stream without a broad account-level stream lookup."""

    parts = subject.split(".")
    if len(parts) < 4 or parts[:2] != ["bridge", "v1"]:
        raise ValueError(f"not a Bridge subject filter: {subject}")
    family = parts[2]
    if family == "event":
        return EVENTS_STREAM
    if family == "dead":
        return DEAD_LETTER_STREAM
    if family in {"inbox", "capability", "room", "result", "control"}:
        return WORK_STREAM
    raise ValueError(f"unsupported Bridge subject family: {family}")


class BridgeSubscription:
    def __init__(
        self,
        transport: JetStreamTransport,
        subscription: JetStreamContext.PullSubscription,
    ) -> None:
        self._transport = transport
        self._subscription = subscription

    async def fetch(self, *, batch: int = 1, timeout: float = 1.0) -> list[BridgeDelivery]:
        if batch < 1 or timeout <= 0:
            raise ValueError("batch and timeout must be positive")
        try:
            messages = await self._subscription.fetch(batch=batch, timeout=timeout)
        except TimeoutError:
            return []
        deliveries = [BridgeDelivery(self._transport, message) for message in messages]
        for delivery in deliveries:
            await delivery._observe(BrokerActivityKind.DELIVERED)
        return deliveries


class BridgeDelivery:
    """A delivered envelope whose settlement is always an explicit caller choice."""

    def __init__(self, transport: JetStreamTransport, message: Msg) -> None:
        self._transport = transport
        self._message = message
        self._envelope: BridgeEnvelope | None = None
        self._settled = False

    @property
    def subject(self) -> str:
        return self._message.subject

    @property
    def payload(self) -> bytes:
        return self._message.data

    @property
    def headers(self) -> Mapping[str, str]:
        return self._message.headers or {}

    @property
    def envelope(self) -> BridgeEnvelope:
        if self._envelope is None:
            self._envelope = decode_envelope(self.payload)
        return self._envelope

    @property
    def delivery_count(self) -> int:
        return self._message.metadata.num_delivered

    @property
    def settled(self) -> bool:
        return self._settled

    async def ack(self) -> None:
        self._ensure_unsettled()
        await self._message.ack()
        self._settled = True
        await self._observe(BrokerActivityKind.ACKNOWLEDGED)

    async def in_progress(self) -> None:
        self._ensure_unsettled()
        await self._message.in_progress()
        await self._observe(BrokerActivityKind.LEASE_EXTENDED)

    async def nak(self, *, delay_seconds: float | None = None, reason: str = "retry") -> None:
        """Retry according to envelope policy or atomically move the final attempt to DLQ."""

        self._ensure_unsettled()
        try:
            envelope = self.envelope
        except ValueError:
            await self.dead_letter(reason="invalid_envelope")
            return
        now = datetime.now(UTC)
        expires_at = envelope.delivery.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        expired = expires_at is not None and expires_at <= now
        if expired or self.delivery_count >= envelope.delivery.max_attempts:
            await self.dead_letter(reason="expired" if expired else reason)
            return
        delay = envelope.delivery.retry_backoff_seconds if delay_seconds is None else delay_seconds
        if delay < 0:
            raise ValueError("negative NAK delay is invalid")
        await self._message.nak(delay=delay)
        self._settled = True
        await self._observe(
            BrokerActivityKind.RETRY_SCHEDULED,
            detail={"delay_seconds": delay, "reason": reason},
        )

    async def dead_letter(self, *, reason: str) -> PublishedMessage:
        """Publish the original bytes durably before acknowledging the source message."""

        self._ensure_unsettled()
        source_headers = self.headers
        headers = {
            "Nats-Msg-Id": f"dlq-{source_headers.get('X-Bridge-Message-Id', 'unknown')}",
            "X-Bridge-Dead-Reason": reason,
            "X-Bridge-Original-Subject": self.subject,
            "X-Bridge-Delivery-Count": str(self.delivery_count),
        }
        acknowledgement = await self._transport.jetstream.publish(
            dead_letter_subject(self.subject),
            self.payload,
            headers=headers,
        )
        await self._message.ack()
        self._settled = True
        await self._observe(
            BrokerActivityKind.DEAD_LETTERED,
            detail={
                "reason": reason,
                "dead_letter_subject": dead_letter_subject(self.subject),
                "dead_letter_sequence": acknowledgement.seq,
            },
        )
        return PublishedMessage(
            stream=acknowledgement.stream,
            sequence=acknowledgement.seq,
            duplicate=bool(acknowledgement.duplicate),
        )

    def _ensure_unsettled(self) -> None:
        if self._settled:
            raise RuntimeError("delivery is already settled")

    async def _observe(
        self,
        kind: BrokerActivityKind,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        headers = self.headers
        metadata = self._message.metadata
        await self._transport._observe(
            BrokerActivity(
                kind=kind,
                subject=self.subject,
                message_id=headers.get("X-Bridge-Message-Id"),
                correlation_id=headers.get("X-Bridge-Correlation-Id"),
                stream=metadata.stream,
                stream_sequence=metadata.sequence.stream,
                consumer=metadata.consumer,
                consumer_sequence=metadata.sequence.consumer,
                delivery_count=metadata.num_delivered,
                detail=detail or {},
            )
        )
