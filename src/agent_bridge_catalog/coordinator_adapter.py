"""Adapters between the async coordinator engine and Catalog persistence."""

from __future__ import annotations

import asyncio
import json

from agent_bridge_coordinator.models import (
    ActivationSnapshot,
    BudgetUsage,
    CoordinatorAction,
    CoordinatorSession,
)
from agent_bridge_protocol.models import (
    CoordinatorIntakeStatus,
    RoleCheckpoint,
    RoleLease,
    RoleReport,
    WorkRequest,
)

from .coordinator_store import CoordinatorStore
from .repository import CatalogRepository


class AsyncCoordinatorPersistence:
    """Keep blocking SQLAlchemy sessions outside the coordinator event loop."""

    def __init__(
        self,
        store: CoordinatorStore,
        repository: CatalogRepository,
        *,
        node_id: str,
        environment_id: str,
    ) -> None:
        self.store = store
        self.repository = repository
        self.node_id = node_id
        self.environment_id = environment_id

    async def begin_activation(
        self,
        role_id: str,
        holder_id: str,
        *,
        request: WorkRequest,
        intake_id: str | None,
        ttl_seconds: float,
    ) -> ActivationSnapshot:
        result = await asyncio.to_thread(
            self.store.begin_activation,
            role_id,
            holder_id=holder_id,
            intake_request_id=intake_id,
            ttl_seconds=ttl_seconds,
            authority=request.authority,
        )
        role = await asyncio.to_thread(self.store.roles.get_role, role_id)
        if role is None:  # defensive: begin_activation already validates this
            raise LookupError(f"unknown role: {role_id}")
        latest = await asyncio.to_thread(self.store.roles.get_latest_checkpoint, role_id)
        received = await asyncio.to_thread(self.store.roles.list_reports, recipient_role_id=role_id)
        children = await asyncio.to_thread(self.store.roles.list_roles, parent_role_id=role_id)
        child_ids = {item.role_id for item in children}
        rollups = await asyncio.to_thread(self.store.list_rollups, role_id)
        activation_id = str(result["activation"]["activation_id"])
        activation = await asyncio.to_thread(self.store.get_activation, activation_id)
        if activation is None:
            raise RuntimeError("activation disappeared after it was created")

        provider_thread_id: str | None = None
        workspace: str | None = None
        if role.current_conversation_id is not None:
            conversation = await asyncio.to_thread(
                self.repository.get, role.current_conversation_id
            )
            if conversation is not None:
                provider_thread_id = conversation.provider_thread_id
                workspace = conversation.cwd

        return ActivationSnapshot(
            activation_id=activation_id,
            role=role,
            lease=RoleLease.model_validate(result["lease"]),
            request=request,
            latest_checkpoint=latest,
            received_reports=received,
            child_reports=[item for item in received if item.reporting_role_id in child_ids],
            stale_child_role_ids=[item.child_role_id for item in rollups if item.stale],
            conversation_history=json.loads(
                json.dumps(
                    await asyncio.to_thread(self.store.roles.list_role_conversations, role_id),
                    default=str,
                )
            ),
            provider_thread_id=provider_thread_id,
            workspace=workspace,
            context=json.loads(json.dumps(result["context"], default=str)),
            usage=BudgetUsage(
                attempts=activation.usage.attempts_used,
                input_tokens=activation.usage.tokens_used,
                cost_usd=activation.usage.cost_used_usd,
            ),
        )

    async def renew_activation(self, activation_id: str, ttl_seconds: float) -> RoleLease:
        return await asyncio.to_thread(self.store.renew_activation, activation_id, ttl_seconds)

    async def assert_activation_active(self, activation_id: str) -> None:
        await asyncio.to_thread(self.store.assert_activation_active, activation_id)

    async def bind_conversation(
        self, activation_id: str, coordinator_session: CoordinatorSession
    ) -> CoordinatorSession:
        activation = await asyncio.to_thread(self.store.get_activation, activation_id)
        if activation is None:
            raise LookupError("activation not found")
        provider_thread_id = (
            coordinator_session.provider_thread_id or coordinator_session.conversation_id
        )
        conversation = await asyncio.to_thread(
            self.repository.upsert_discovered,
            {
                "provider": "codex",
                "provider_thread_id": provider_thread_id,
                "title": f"Coordinator: {activation.role_id}",
                "preview": coordinator_session.handoff_summary or "Durable coordinator session",
                "status": "active",
                "source": "coordinator-sdk",
                "cwd": coordinator_session.cwd,
                "raw_metadata": {"role_id": activation.role_id},
            },
            node_id=self.node_id,
            environment_id=self.environment_id,
        )
        mapped = coordinator_session.model_copy(
            update={
                "conversation_id": conversation.conversation_id,
                "provider_thread_id": provider_thread_id,
            }
        )
        await asyncio.to_thread(
            self.store.set_activation_conversation,
            activation_id,
            mapped.conversation_id,
        )
        role = await asyncio.to_thread(self.store.roles.get_role, activation.role_id)
        if role is None:
            raise LookupError(f"unknown role: {activation.role_id}")
        if role.current_conversation_id is None:
            await asyncio.to_thread(
                self.store.roles.attach_conversation,
                role.role_id,
                mapped.conversation_id,
                mapped.handoff_summary,
            )
        elif role.current_conversation_id != mapped.conversation_id:
            await asyncio.to_thread(
                self.store.roles.rotate_conversation,
                role.role_id,
                mapped.conversation_id,
                mapped.handoff_summary,
            )
        return mapped

    async def commit_checkpoint(self, activation_id: str, checkpoint: RoleCheckpoint) -> None:
        await asyncio.to_thread(self.store.commit_checkpoint, activation_id, checkpoint)

    async def record_usage(self, activation_id: str, usage: BudgetUsage) -> None:
        await asyncio.to_thread(
            self.store.record_usage,
            activation_id,
            tokens=usage.total_tokens,
            cost_usd=usage.cost_usd,
            attempts=usage.attempts,
        )

    async def complete_activation(
        self,
        activation_id: str,
        *,
        conversation_id: str,
        proposed_actions: list[CoordinatorAction],
        executed_action_ids: list[str],
        attention_required: str | None,
    ) -> None:
        del conversation_id
        activation = await asyncio.to_thread(self.store.get_activation, activation_id)
        if activation is None:
            raise LookupError("activation not found")
        if activation.intake_request_id:
            awaiting = bool(attention_required and proposed_actions)
            await asyncio.to_thread(
                self.store.update_intake,
                activation.intake_request_id,
                proposed_actions=[item.model_dump(mode="json") for item in proposed_actions],
                proposed_topology=_topology(proposed_actions),
                attention_required=attention_required,
                executed=bool(executed_action_ids),
            )
            intake_status = (
                CoordinatorIntakeStatus.AWAITING_APPROVAL
                if awaiting
                else CoordinatorIntakeStatus.COMPLETED
            )
        else:
            intake_status = CoordinatorIntakeStatus.COMPLETED
        await asyncio.to_thread(
            self.store.complete_activation,
            activation_id,
            intake_status=intake_status,
            intake_executed=bool(executed_action_ids),
            attention_required=attention_required,
        )

    async def fail_activation(self, activation_id: str, error: str) -> None:
        await asyncio.to_thread(self.store.fail_activation, activation_id, error)

    async def release_activation(self, activation_id: str) -> None:
        await asyncio.to_thread(self.store.release_activation, activation_id)

    async def submit_child_report(self, report: RoleReport) -> None:
        await asyncio.to_thread(self.store.submit_child_report, report)


def _topology(actions: list[CoordinatorAction]) -> dict[str, object]:
    return {
        "nodes": [
            {
                "id": item.action_id,
                "type": str(item.type),
                "target_id": item.target_id,
                "scope": item.scope,
            }
            for item in actions
        ],
        "edges": [],
    }
