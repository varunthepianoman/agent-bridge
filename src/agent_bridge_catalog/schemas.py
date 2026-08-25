"""Strict HTTP contracts for trusted single-user Bridge nodes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NodeProvisionRequest(Input):
    node_id: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=500)
    platform: str = Field(min_length=1, max_length=80)
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    credential: str | None = Field(default=None, min_length=24)


class NodeRegistration(Input):
    node_id: str = Field(min_length=1, max_length=160)
    display_name: str | None = Field(default=None, max_length=500)
    platform: str | None = Field(default=None, max_length=80)
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EnvironmentRegistration(Input):
    environment_id: str = Field(min_length=1, max_length=160)
    display_name: str | None = Field(default=None, max_length=500)
    kind: str = Field(default="native", min_length=1, max_length=80)
    root_path: str | None = None
    exclude_providers: list[str] = Field(default_factory=list)
    exclude_repositories: list[str] = Field(default_factory=list)
    exclude_folders: list[str] = Field(default_factory=list)
    exclude_conversation_ids: list[str] = Field(default_factory=list)
    include_transcript_text: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class NodeCatalogSyncRequest(Input):
    registration: NodeRegistration
    conversations: list[dict[str, Any]] = Field(default_factory=list, max_length=10_000)
    environments: list[EnvironmentRegistration] = Field(default_factory=list)


class NodeHeartbeatRequest(Input):
    node_id: str = Field(min_length=1, max_length=160)
    ttl_seconds: int = Field(default=90, ge=15, le=600)
    capabilities: list[str] | None = None
    metadata: dict[str, Any] | None = None


class NodeCommandClaimRequest(Input):
    node_id: str = Field(min_length=1, max_length=160)


class NodeCommandResultRequest(Input):
    node_id: str = Field(min_length=1, max_length=160)
    claim_token: str = Field(min_length=24)
    status: str = Field(pattern="^(succeeded|blocked|failed|cancelled)$")
    detail: str | None = Field(default=None, max_length=50_000)
    output: dict[str, Any] = Field(default_factory=dict)


class NodeTurnEventRequest(Input):
    event_id: str = Field(min_length=1, max_length=500)
    node_id: str = Field(min_length=1, max_length=160)
    environment_id: str = Field(min_length=1, max_length=160)
    provider: str = Field(pattern="^codex$")
    provider_thread_id: str = Field(min_length=1, max_length=500)
    provider_turn_id: str = Field(min_length=1, max_length=500)
    command_id: str = Field(min_length=1, max_length=160)
    status: str = Field(pattern="^(completed|failed|interrupted)$")
    detail: str | None = Field(default=None, max_length=50_000)
