"""Strict coordinator input/output and activation records."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_bridge_protocol.models import (
    ArtifactRef,
    CoordinatorRole,
    RoleCheckpoint,
    RoleLease,
    RoleReport,
    RoleStatus,
    WorkRequest,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoordinatorActionType(StrEnum):
    RECOMMEND = "recommend"
    DELEGATE = "delegate"
    EXECUTE = "execute"
    RETRY = "retry"
    CREATE_WORK = "create_work"
    CREATE_ROLE = "create_role"
    RELATE = "relate"
    REPORT = "report"


class CoordinatorAction(StrictModel):
    action_id: str = Field(min_length=1)
    type: CoordinatorActionType
    summary: str = Field(min_length=1)
    target_id: str | None = None
    capability: str | None = None
    scope: str | None = None
    expands_scope: bool = False
    attempt_count: int = Field(default=1, ge=1)
    estimated_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class CheckpointDraft(StrictModel):
    objective: str = Field(min_length=1)
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
    parent_summary: str = Field(min_length=1)


class CoordinatorModelOutput(StrictModel):
    checkpoint: CheckpointDraft
    actions: list[CoordinatorAction] = Field(default_factory=list)
    attention_required: str | None = None


class BudgetUsage(StrictModel):
    attempts: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    cost_known: bool = True

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class CoordinatorSession(StrictModel):
    conversation_id: str
    provider_thread_id: str | None = None
    cwd: str | None = None
    is_replacement: bool = False
    handoff_summary: str | None = None
    state: dict[str, Any] = Field(default_factory=dict)


class CoordinatorTurn(StrictModel):
    output: CoordinatorModelOutput
    session: CoordinatorSession
    usage: BudgetUsage = Field(default_factory=BudgetUsage)


class ActivationSnapshot(StrictModel):
    activation_id: str
    role: CoordinatorRole
    lease: RoleLease
    request: WorkRequest
    latest_checkpoint: RoleCheckpoint | None = None
    received_reports: list[RoleReport] = Field(default_factory=list)
    child_reports: list[RoleReport] = Field(default_factory=list)
    stale_child_role_ids: list[str] = Field(default_factory=list)
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)
    provider_thread_id: str | None = None
    workspace: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    usage: BudgetUsage = Field(default_factory=BudgetUsage)


class CoordinatorActivationResult(StrictModel):
    activation_id: str | None = None
    role_id: str | None = None
    mode: str
    status: str
    checkpoint: RoleCheckpoint | None = None
    proposed_actions: list[CoordinatorAction] = Field(default_factory=list)
    executed_action_ids: list[str] = Field(default_factory=list)
    attention_required: str | None = None
    conversation_id: str | None = None
    completed_at: datetime | None = None
