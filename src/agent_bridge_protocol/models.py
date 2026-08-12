"""Version 1 public contracts for Agent Bridge and AI Work Catalog."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION: Final = "agent-bridge/v1"


def utc_now() -> datetime:
    return datetime.now(UTC)


class ContractModel(BaseModel):
    """Strict base model; deliberate extension points use ``extensions``."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_assignment=True)


class VersionedModel(ContractModel):
    schema_version: Literal["agent-bridge/v1"] = SCHEMA_VERSION


class AutonomyMode(StrEnum):
    MANUAL = "manual"
    ADVISE = "advise"
    DELEGATE = "delegate"
    AUTONOMOUS = "autonomous"


class Availability(StrEnum):
    REACHABLE = "reachable"
    OFFLINE = "offline"
    ENVIRONMENT_UNAVAILABLE = "environment_unavailable"
    IDLE = "idle"
    ACTIVE = "active"
    WAITING_FOR_USER = "waiting_for_user"
    ARCHIVED = "archived"
    MISSING = "missing"


class ExecutionStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    DEAD_LETTERED = "dead_lettered"


class RoleStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class MessageKind(StrEnum):
    MESSAGE = "message"
    REQUEST = "request"
    EVENT = "event"
    RESPONSE = "response"
    CONTROL = "control"
    PROPOSAL = "proposal"
    CRITIQUE = "critique"
    REVISION = "revision"
    ACCEPTANCE = "acceptance"


class ExecutionOperation(StrEnum):
    NEW_EXECUTION = "new_execution"
    RESUME_CONVERSATION = "resume_conversation"
    WAKE_ENDPOINT = "wake_endpoint"
    INVOKE_ADAPTER = "invoke_adapter"


class EndpointKind(StrEnum):
    CONVERSATION = "conversation"
    ROLE = "role"
    NODE = "node"
    CAPABILITY = "capability"
    ROOM = "room"
    ENDPOINT = "endpoint"


class CollaborationOperation(StrEnum):
    DIRECT = "direct"
    REQUEST = "request"
    REPLY = "reply"
    CAPABILITY = "capability"
    FANOUT = "fanout"
    PROPOSAL = "proposal"
    CRITIQUE = "critique"
    REVISION = "revision"
    ACCEPTANCE = "acceptance"


class RepositoryRef(ContractModel):
    repository_id: str
    url: str | None = None
    name: str | None = None


class EnvironmentRef(ContractModel):
    environment_id: str
    node_id: str
    kind: str | None = None
    name: str | None = None


class ProviderThreadRef(ContractModel):
    provider: str
    provider_thread_id: str
    locator: dict[str, Any] = Field(default_factory=dict)


class ConversationRef(ContractModel):
    conversation_id: str
    provider: str
    provider_thread_id: str
    node_id: str
    environment_id: str | None = None
    title: str | None = None
    repository_id: str | None = None
    worktree_path: str | None = None
    branch: str | None = None
    pull_request: str | None = None
    parent_conversation_id: str | None = None
    status: Availability = Availability.IDLE
    last_summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    last_activity_at: datetime | None = None
    locator: dict[str, Any] = Field(default_factory=dict)


class AgentRef(ContractModel):
    agent_id: str
    provider: str | None = None
    conversation_id: str | None = None


class NodeRef(ContractModel):
    node_id: str
    name: str | None = None


class WorkRef(ContractModel):
    work_id: str
    title: str | None = None


class CapabilityRef(ContractModel):
    capability_id: str
    name: str | None = None


class ArtifactRef(ContractModel):
    artifact_id: str | None = None
    name: str
    uri: str
    media_type: str | None = None
    size: int | None = Field(default=None, ge=0)
    sha256: str | None = None
    retention_until: datetime | None = None
    sensitivity: str | None = None

    @field_validator("sha256")
    @classmethod
    def sha256_is_hex_digest(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in value)
        ):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        return value


class EndpointRef(ContractModel):
    kind: EndpointKind
    id: str


class RegisteredEndpoint(VersionedModel):
    endpoint_id: str
    display_name: str
    address: EndpointRef
    capabilities: list[str] = Field(default_factory=list)
    work_ids: list[str] = Field(default_factory=list)
    status: str = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CollaborationRoom(VersionedModel):
    room_id: str
    name: str
    work_id: str | None = None
    durable: bool = True
    members: list[EndpointRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CollaborationMessage(VersionedModel):
    collaboration_id: str
    operation: CollaborationOperation
    sender: EndpointRef
    destinations: list[EndpointRef] = Field(min_length=1)
    body: dict[str, Any] = Field(min_length=1)
    work_id: str | None = None
    correlation_id: str
    causation_id: str | None = None
    reply_to: EndpointRef | None = None
    state: str = "pending"
    bridge_message_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Relationship(VersionedModel):
    relationship_id: str
    source: EndpointRef
    target: EndpointRef
    type: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def relationship_type_is_token(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("relationship type cannot contain whitespace")
        return value


class DeliveryPolicy(ContractModel):
    expires_at: datetime | None = None
    max_attempts: int = Field(default=3, ge=1, le=100)
    retry_backoff_seconds: float = Field(default=5.0, ge=0)
    acknowledgement_timeout_seconds: float = Field(default=60.0, gt=0)


class BridgeEnvelope(VersionedModel):
    message_id: str
    kind: MessageKind
    sender: EndpointRef
    destination: EndpointRef
    body: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    causation_id: str | None = None
    reply_to: EndpointRef | None = None
    work_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    delivery: DeliveryPolicy = Field(default_factory=DeliveryPolicy)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)


class ExecutionRequest(VersionedModel):
    execution_id: str
    operation: ExecutionOperation
    instruction: str = Field(min_length=1)
    target: EndpointRef
    work_id: str | None = None
    conversation_id: str | None = None
    cwd: str | None = Field(default=None, min_length=1)
    adapter: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    delivery: DeliveryPolicy = Field(default_factory=DeliveryPolicy)
    requested_at: datetime = Field(default_factory=utc_now)
    requested_by: EndpointRef | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def operation_has_locator(self) -> Self:
        if self.operation == ExecutionOperation.RESUME_CONVERSATION and not self.conversation_id:
            raise ValueError("resume_conversation requires conversation_id")
        if self.operation == ExecutionOperation.INVOKE_ADAPTER and not self.adapter:
            raise ValueError("invoke_adapter requires adapter")
        return self


class ExecutionAttempt(VersionedModel):
    attempt_id: str
    execution_id: str
    attempt_number: int = Field(ge=1)
    node_id: str | None = None
    status: ExecutionStatus = ExecutionStatus.QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class ExecutionLease(VersionedModel):
    execution_id: str
    holder_id: str
    fencing_token: int = Field(ge=1)
    acquired_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime

    @model_validator(mode="after")
    def expiry_follows_acquisition(self) -> Self:
        if self.expires_at <= self.acquired_at:
            raise ValueError("lease expires_at must be after acquired_at")
        return self


class ExecutionProgress(VersionedModel):
    execution_id: str
    attempt_id: str
    sequence: int = Field(ge=0)
    status: ExecutionStatus = ExecutionStatus.RUNNING
    summary: str
    percent: float | None = Field(default=None, ge=0, le=100)
    occurred_at: datetime = Field(default_factory=utc_now)


class ExecutionResult(VersionedModel):
    execution_id: str
    attempt_id: str
    node_id: str | None = None
    status: Literal["succeeded", "blocked"] = "succeeded"
    summary: str
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    completed_at: datetime = Field(default_factory=utc_now)


class ExecutionFailure(VersionedModel):
    execution_id: str
    attempt_id: str
    node_id: str | None = None
    status: Literal["failed", "cancelled", "expired", "dead_lettered"]
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    failed_at: datetime = Field(default_factory=utc_now)


class CapabilityAdvertisement(ContractModel):
    capability_id: str
    name: str
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NodeRegistration(VersionedModel):
    node_id: str
    name: str
    environments: list[EnvironmentRef] = Field(default_factory=list)
    capabilities: list[CapabilityAdvertisement] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    registered_at: datetime = Field(default_factory=utc_now)


class NodeHeartbeat(VersionedModel):
    node_id: str
    availability: Availability = Availability.REACHABLE
    observed_at: datetime = Field(default_factory=utc_now)
    active_execution_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkItem(VersionedModel):
    work_id: str
    title: str
    objective: str | None = None
    status: str = "active"
    repository_id: str | None = None
    branch: str | None = None
    pull_request: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    extensions: dict[str, Any] = Field(default_factory=dict)


class WorkConversationLink(VersionedModel):
    work_id: str
    conversation_id: str
    relationship: str = "related_to"
    attached_at: datetime = Field(default_factory=utc_now)
    extensions: dict[str, Any] = Field(default_factory=dict)


class CoordinatorRole(VersionedModel):
    role_id: str
    role_type: str
    scope: str
    charter: str
    authority_profile: str
    autonomy_mode: AutonomyMode = AutonomyMode.DELEGATE
    parent_role_id: str | None = None
    current_conversation_id: str | None = None
    checkpoint_version: int = Field(default=0, ge=0)
    status: RoleStatus = RoleStatus.DRAFT
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    extensions: dict[str, Any] = Field(default_factory=dict)


class RoleConversationLink(VersionedModel):
    role_id: str
    conversation_id: str
    relationship: Literal["current", "previous", "handoff"] = "current"
    attached_at: datetime = Field(default_factory=utc_now)
    detached_at: datetime | None = None
    handoff_reason: str | None = None


class ConversationHandoff(VersionedModel):
    handoff_id: str
    source_conversation_id: str
    target_environment_id: str | None = None
    role_id: str | None = None
    work_id: str | None = None
    summary: str
    decisions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class RoleCheckpoint(VersionedModel):
    role_id: str
    version: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    objective: str
    charter: str
    authority_profile: str
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
    created_at: datetime = Field(default_factory=utc_now)


class RoleReport(VersionedModel):
    report_id: str
    reporting_role_id: str
    recipient_role_id: str
    checkpoint_version: int = Field(ge=1)
    status: RoleStatus
    summary: str
    decisions: list[str] = Field(default_factory=list)
    evidence: list[ArtifactRef] = Field(default_factory=list)
    attention_required: str | None = None
    recommended_action: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class RoleEvent(VersionedModel):
    event_id: str
    role_id: str
    type: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    actor: EndpointRef | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=utc_now)


class RoleLease(VersionedModel):
    role_id: str
    holder_id: str
    fencing_token: int = Field(ge=1)
    acquired_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime

    @model_validator(mode="after")
    def expiry_follows_acquisition(self) -> Self:
        if self.expires_at <= self.acquired_at:
            raise ValueError("lease expires_at must be after acquired_at")
        return self


class AuthorityLimits(ContractModel):
    max_parallel_executions: int = Field(default=1, ge=1)
    max_attempts: int = Field(default=3, ge=1)
    token_budget: int | None = Field(default=None, gt=0)
    cost_budget_usd: float | None = Field(default=None, gt=0)
    deadline: datetime | None = None
    allowed_capabilities: list[str] = Field(default_factory=list)
    allowed_work_ids: list[str] = Field(default_factory=list)
    allowed_repository_ids: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    may_expand_scope: bool = False


class WorkRequest(VersionedModel):
    request_id: str
    objective: str = Field(min_length=1)
    mode: AutonomyMode = AutonomyMode.DELEGATE
    requested_by: EndpointRef
    work_id: str | None = None
    target_role_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    authority: AuthorityLimits = Field(default_factory=AuthorityLimits)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    extensions: dict[str, Any] = Field(default_factory=dict)


class CoordinatorIntakeStatus(StrEnum):
    SUBMITTED = "submitted"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class CoordinatorIntake(VersionedModel):
    request_id: str
    request: WorkRequest
    status: CoordinatorIntakeStatus = CoordinatorIntakeStatus.SUBMITTED
    routed_work_id: str | None = None
    routed_role_id: str | None = None
    proposed_actions: list[dict[str, Any]] = Field(default_factory=list)
    proposed_topology: dict[str, Any] = Field(default_factory=dict)
    attention_required: str | None = None
    approval_required: bool = False
    executed: bool = False
    decision_note: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AuthorityUsage(ContractModel):
    tokens_used: int = Field(default=0, ge=0)
    cost_used_usd: float = Field(default=0, ge=0)
    attempts_used: int = Field(default=0, ge=0)
    active_executions: int = Field(default=0, ge=0)
    total_executions: int = Field(default=0, ge=0)


class CoordinatorActivationStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class CoordinatorActivation(VersionedModel):
    activation_id: str
    role_id: str
    intake_request_id: str | None = None
    holder_id: str
    fencing_token: int = Field(ge=1)
    status: CoordinatorActivationStatus = CoordinatorActivationStatus.ACTIVE
    checkpoint_version_before: int = Field(ge=0)
    checkpoint_version_after: int | None = Field(default=None, ge=1)
    conversation_id: str | None = None
    authority: AuthorityLimits = Field(default_factory=AuthorityLimits)
    usage: AuthorityUsage = Field(default_factory=AuthorityUsage)
    started_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    error: str | None = None


class RoleRollupState(VersionedModel):
    parent_role_id: str
    child_role_id: str
    incorporated_checkpoint_version: int = Field(default=0, ge=0)
    current_checkpoint_version: int = Field(default=0, ge=0)
    report_id: str | None = None
    stale: bool = False
    updated_at: datetime = Field(default_factory=utc_now)
