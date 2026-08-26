"""Minimal public contracts for the conversation-centric Agent Bridge."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION: Final = "agent-bridge/v1"
DeliveryStrategy = Literal["mailbox", "queue", "steer-or-queue"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)


class EndpointKind(StrEnum):
    CONVERSATION = "conversation"
    NODE = "node"
    ROOM = "room"
    ENDPOINT = "endpoint"


class MessageKind(StrEnum):
    MESSAGE = "message"
    REQUEST = "request"
    EVENT = "event"
    RESPONSE = "response"
    CONTROL = "control"


class EndpointRef(ContractModel):
    kind: EndpointKind
    id: str = Field(min_length=1, max_length=160)


class ArtifactRef(ContractModel):
    artifact_id: str | None = None
    name: str
    uri: str
    media_type: str | None = None
    size: int | None = Field(default=None, ge=0)
    sha256: str | None = None
    sensitivity: str | None = None

    @field_validator("sha256")
    @classmethod
    def sha256_is_digest(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in value)
        ):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        return value


class DeliveryPolicy(ContractModel):
    strategy: DeliveryStrategy = "mailbox"
    expires_at: datetime | None = None
    max_attempts: int = Field(default=3, ge=1, le=100)
    retry_backoff_seconds: float = Field(default=5.0, ge=0)
    acknowledgement_timeout_seconds: float = Field(default=60.0, gt=0)


class BridgeEnvelope(ContractModel):
    schema_version: Literal["agent-bridge/v1"] = SCHEMA_VERSION
    message_id: str = Field(min_length=1, max_length=160)
    kind: MessageKind
    sender: EndpointRef
    destination: EndpointRef
    body: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = Field(default=None, max_length=160)
    causation_id: str | None = Field(default=None, max_length=160)
    reply_to: EndpointRef | None = None
    created_at: datetime = Field(default_factory=utc_now)
    delivery: DeliveryPolicy = Field(default_factory=DeliveryPolicy)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_causation(self) -> BridgeEnvelope:
        if self.causation_id == self.message_id:
            raise ValueError("a message cannot cause itself")
        return self
