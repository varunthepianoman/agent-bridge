from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agent_bridge_protocol.models import (
    ArtifactRef,
    AutonomyMode,
    EndpointRef,
    RoleStatus,
)


class ConversationMetadataUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=20_000)
    tags: list[str] | None = None
    pinned: bool | None = None
    hidden: bool | None = None
    archived: bool | None = None

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class ResumeRequest(BaseModel):
    conversation_id: str
    launch: bool = False


class SyncRequest(BaseModel):
    include_turns: bool = True


class WorkItemCreate(BaseModel):
    work_id: str | None = None
    title: str = Field(min_length=1, max_length=500)
    objective: str | None = Field(default=None, max_length=20_000)
    status: str = Field(default="active", min_length=1, max_length=80)
    repository_id: str | None = None
    branch: str | None = None
    pull_request: str | None = None
    tags: list[str] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)


class WorkItemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    objective: str | None = Field(default=None, max_length=20_000)
    status: str | None = Field(default=None, min_length=1, max_length=80)
    repository_id: str | None = None
    branch: str | None = None
    pull_request: str | None = None
    tags: list[str] | None = None
    extensions: dict[str, Any] | None = None

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class WorkConversationCreate(BaseModel):
    conversation_id: str = Field(min_length=1)


class RelationshipCreate(BaseModel):
    relationship_id: str | None = None
    source: EndpointRef
    target: EndpointRef
    type: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def relationship_type_is_token(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("relationship type cannot contain whitespace")
        return value


class CoordinatorRoleCreate(BaseModel):
    role_id: str | None = None
    role_type: str = Field(min_length=1, max_length=100)
    scope: str = Field(min_length=1, max_length=500)
    charter: str = Field(min_length=1, max_length=20_000)
    authority_profile: str = Field(min_length=1, max_length=200)
    autonomy_mode: AutonomyMode = AutonomyMode.DELEGATE
    parent_role_id: str | None = None
    current_conversation_id: str | None = None
    status: RoleStatus = RoleStatus.DRAFT
    extensions: dict[str, Any] = Field(default_factory=dict)


class CoordinatorRoleUpdate(BaseModel):
    role_type: str | None = Field(default=None, min_length=1, max_length=100)
    scope: str | None = Field(default=None, min_length=1, max_length=500)
    charter: str | None = Field(default=None, min_length=1, max_length=20_000)
    authority_profile: str | None = Field(default=None, min_length=1, max_length=200)
    autonomy_mode: AutonomyMode | None = None
    parent_role_id: str | None = None
    status: RoleStatus | None = None
    extensions: dict[str, Any] | None = None

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class RoleCheckpointCreate(BaseModel):
    fencing_token: int = Field(ge=1)
    objective: str = Field(min_length=1)
    charter: str = Field(min_length=1)
    authority_profile: str = Field(min_length=1)
    status: RoleStatus
    decisions: list[str] = Field(default_factory=list)
    completed_delegations: list[str] = Field(default_factory=list)
    active_delegations: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    evidence: list[ArtifactRef] = Field(default_factory=list)
    current_plan: list[str] = Field(default_factory=list)
    recommended_next_action: str | None = None
    parent_summary: str


class RoleReportCreate(BaseModel):
    report_id: str | None = None
    recipient_role_id: str = Field(min_length=1)
    checkpoint_version: int = Field(ge=1)
    status: RoleStatus
    summary: str = Field(min_length=1)
    decisions: list[str] = Field(default_factory=list)
    evidence: list[ArtifactRef] = Field(default_factory=list)
    attention_required: str | None = None
    recommended_action: str | None = None


class RoleConversationHandoff(BaseModel):
    conversation_id: str = Field(min_length=1)
    handoff_summary: str | None = Field(default=None, max_length=50_000)


class RoleLeaseRequest(BaseModel):
    holder_id: str = Field(min_length=1)
    ttl_seconds: float = Field(default=300, gt=0, le=86_400)


class RoleLeaseRenewRequest(BaseModel):
    holder_id: str = Field(min_length=1)
    fencing_token: int = Field(ge=1)
    ttl_seconds: float = Field(default=300, gt=0, le=86_400)


class RoleLeaseReleaseRequest(BaseModel):
    holder_id: str = Field(min_length=1)
    fencing_token: int = Field(ge=1)


class HandoffDocument(BaseModel):
    role_id: str
    generated_at: datetime
    markdown: str


class NodeProvisionRequest(BaseModel):
    node_id: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=500)
    platform: str = Field(min_length=1, max_length=80)
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    credential: str | None = Field(default=None, min_length=24)


class NodeRegistration(BaseModel):
    node_id: str = Field(min_length=1, max_length=160)
    display_name: str | None = Field(default=None, max_length=500)
    platform: str | None = Field(default=None, max_length=80)
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EnvironmentRegistration(BaseModel):
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


class NodeCatalogSyncRequest(BaseModel):
    registration: NodeRegistration
    conversations: list[dict[str, Any]] = Field(default_factory=list, max_length=10_000)
    environments: list[EnvironmentRegistration] = Field(default_factory=list)


class NodeHeartbeatRequest(BaseModel):
    node_id: str = Field(min_length=1, max_length=160)
    ttl_seconds: int = Field(default=90, ge=15, le=600)
    capabilities: list[str] | None = None
    metadata: dict[str, Any] | None = None


class NodeCommandClaimRequest(BaseModel):
    node_id: str = Field(min_length=1, max_length=160)


class NodeCommandResultRequest(BaseModel):
    node_id: str = Field(min_length=1, max_length=160)
    claim_token: str = Field(min_length=24)
    status: str = Field(pattern="^(succeeded|blocked|failed|cancelled)$")
    detail: str | None = Field(default=None, max_length=50_000)
    output: dict[str, Any] = Field(default_factory=dict)
