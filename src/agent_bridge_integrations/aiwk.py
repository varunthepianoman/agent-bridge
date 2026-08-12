"""Thin AIWK executor seam; AIWK remains the workflow policy owner."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_bridge_catalog.manual_bridge import ManualBridgeService
from agent_bridge_protocol.models import (
    ArtifactRef,
    DeliveryPolicy,
    EndpointRef,
    ExecutionOperation,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AIWKReference(_StrictModel):
    """Opaque correlation owned and interpreted by AIWK, not Bridge core."""

    project: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    step: str = Field(min_length=1)
    role: str = Field(min_length=1)
    cycle: int = Field(ge=0)
    attempt: int = Field(ge=1)
    workflow_fingerprint: str = Field(min_length=1)

    @field_validator("workflow_fingerprint")
    @classmethod
    def fingerprint_is_namespaced_digest(cls, value: str) -> str:
        algorithm, separator, digest = value.partition(":")
        if not separator or not algorithm or not digest:
            raise ValueError("workflow_fingerprint must use algorithm:digest form")
        return value


class AIWKRoleInvocation(_StrictModel):
    reference: AIWKReference
    instruction: str = Field(min_length=1)
    target: EndpointRef
    work_id: str | None = None
    conversation_id: str | None = None
    adapter: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    delivery: DeliveryPolicy = Field(default_factory=DeliveryPolicy)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)


class AIWKExecutorAdapter:
    """Submit one AIWK-selected role invocation through durable Bridge execution.

    The adapter deliberately does not inspect gates, choose subsequent roles, or
    interpret semantic acceptance. Those decisions stay in AIWK's generated runtime.
    """

    def __init__(self, bridge: ManualBridgeService) -> None:
        self.bridge = bridge

    async def submit(self, invocation: AIWKRoleInvocation) -> dict[str, Any]:
        operation = (
            ExecutionOperation.RESUME_CONVERSATION
            if invocation.conversation_id
            else (
                ExecutionOperation.INVOKE_ADAPTER
                if invocation.adapter
                else ExecutionOperation.NEW_EXECUTION
            )
        )
        result = await self.bridge.submit_request(
            request_input={
                "operation": operation,
                "instruction": invocation.instruction,
                "target": invocation.target,
                "work_id": invocation.work_id,
                "conversation_id": invocation.conversation_id,
                "adapter": invocation.adapter,
                "parameters": invocation.parameters,
                "delivery": invocation.delivery,
                "artifacts": invocation.artifacts,
                "extensions": {
                    **invocation.extensions,
                    "aiwk": invocation.reference.model_dump(mode="json"),
                },
            },
            envelope_options={
                "extensions": {
                    "policy_owner": "aiwk",
                    "aiwk.workflow_fingerprint": invocation.reference.workflow_fingerprint,
                }
            },
        )
        message = result["message"]
        if message.get("status") != "published":
            raise RuntimeError(
                f"AIWK role invocation was not durably published: "
                f"{message.get('error') or message.get('status')}"
            )
        return result
