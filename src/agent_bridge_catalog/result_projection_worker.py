"""Durable JetStream result consumer that advances central execution state."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from pydantic import ValidationError

from agent_bridge_bridge.transport import BridgeDelivery, BridgeSubscription, JetStreamTransport
from agent_bridge_protocol.models import BridgeEnvelope

from .convergence import ConvergenceController
from .manual_bridge import ManualBridgeService
from .nodes import NodeStore
from .repository import CatalogRepository
from .roles import RoleStore

LOGGER = logging.getLogger(__name__)
RESULT_SUBJECT = "bridge.v1.result.>"


class CollaborationSink(Protocol):
    async def ingest_envelope(self, envelope: BridgeEnvelope, *, subject: str) -> None: ...


class ExecutionResultProjectionWorker:
    """Persist result-stream envelopes before acknowledging their JetStream delivery."""

    def __init__(
        self,
        service: ManualBridgeService,
        collaboration_sink: CollaborationSink | None = None,
        *,
        repository: CatalogRepository | None = None,
        role_store: RoleStore | None = None,
        node_store: NodeStore | None = None,
        environment_id: str = "host",
        convergence: ConvergenceController | None = None,
    ) -> None:
        self.service = service
        self.collaboration_sink = collaboration_sink
        self.repository = repository
        self.role_store = role_store
        self.node_store = node_store
        self.environment_id = environment_id
        self.convergence = convergence

    async def subscribe(
        self,
        transport: JetStreamTransport,
        *,
        durable_name: str = "catalog-execution-results-v1",
        ack_wait_seconds: float = 60.0,
    ) -> BridgeSubscription:
        return await transport.subscribe(
            RESULT_SUBJECT,
            durable_name=durable_name,
            ack_wait_seconds=ack_wait_seconds,
        )

    async def run_once(self, subscription: BridgeSubscription, *, timeout: float = 1.0) -> bool:
        deliveries = await subscription.fetch(batch=1, timeout=timeout)
        if not deliveries:
            return False
        await self.process(deliveries[0])
        return True

    async def run_forever(
        self,
        subscription: BridgeSubscription,
        *,
        poll_timeout: float = 1.0,
        idle_delay: float = 0.1,
    ) -> None:
        while True:
            handled = await self.run_once(subscription, timeout=poll_timeout)
            if not handled:
                await asyncio.sleep(idle_delay)

    async def process(self, delivery: BridgeDelivery) -> None:
        envelope: BridgeEnvelope | None = None
        try:
            envelope = delivery.envelope
            if not delivery.subject.startswith("bridge.v1.result."):
                raise ValueError("not a Bridge result subject")
            self.service.ingest_result_envelope(envelope)
            self._project_execution_node(envelope)
            self._project_codex_conversation(envelope)
            if self.convergence is not None and str(envelope.kind) == "response":
                await self.convergence.process(envelope)
        except (LookupError, ValueError, ValidationError):
            if (
                envelope is not None
                and self.collaboration_sink is not None
                and not _looks_like_execution(envelope)
            ):
                try:
                    await self.collaboration_sink.ingest_envelope(
                        envelope, subject=delivery.subject
                    )
                except Exception:
                    LOGGER.exception("Central collaboration reply projection failed")
                    await delivery.nak(reason="central_collaboration_projection_failed")
                    return
                await delivery.ack()
                return
            await delivery.dead_letter(reason="invalid_execution_result")
            return
        except Exception:
            LOGGER.exception("Central execution result projection failed")
            await delivery.nak(reason="central_result_projection_failed")
            return
        # Central SQL state is durable before the source result is acknowledged.
        await delivery.ack()

    def _project_execution_node(self, envelope: BridgeEnvelope) -> None:
        if self.node_store is None:
            return
        execution_id = envelope.body.get("execution_id")
        execution = (
            self.service.get_execution(execution_id)
            if isinstance(execution_id, str)
            else None
        )
        request = execution.get("request") if isinstance(execution, dict) else None
        cwd = request.get("cwd") if isinstance(request, dict) else None
        self.node_store.observe_execution_node(
            envelope.sender.id,
            environment_id=self.environment_id,
            root_path=cwd if isinstance(cwd, str) else None,
        )

    def _project_codex_conversation(self, envelope: BridgeEnvelope) -> None:
        """Catalog and attach a Codex thread returned by a durable execution."""
        if self.repository is None or self.role_store is None:
            return
        output = envelope.body.get("output")
        if not isinstance(output, dict):
            return
        provider_thread_id = output.get("provider_thread_id")
        if not isinstance(provider_thread_id, str) or not provider_thread_id.strip():
            return
        execution_id = envelope.body.get("execution_id")
        if not isinstance(execution_id, str):
            return
        execution = self.service.get_execution(execution_id)
        if execution is None:
            return
        request = execution.get("request")
        if not isinstance(request, dict):
            return
        node_id = envelope.sender.id
        cwd = output.get("cwd") or request.get("cwd")
        final_response = output.get("final_response")
        row = self.repository.upsert_discovered(
            {
                "provider": "codex",
                "provider_thread_id": provider_thread_id,
                "title": _execution_title(execution.get("instruction")),
                "preview": final_response if isinstance(final_response, str) else "",
                "status": "idle",
                "source": "agent_bridge",
                "cwd": cwd if isinstance(cwd, str) else None,
                "last_activity_at": envelope.body.get("completed_at"),
                "raw_metadata": {
                    "execution_id": execution_id,
                    "work_id": execution.get("work_id"),
                    "role_id": _request_role_id(request),
                },
            },
            node_id=node_id,
            environment_id=self.environment_id,
        )
        work_id = execution.get("work_id")
        if isinstance(work_id, str) and not self.role_store.list_relationships(
            source_id=work_id,
            target_kind="conversation",
            target_id=row.conversation_id,
            relationship_type="contains",
        ):
            self.role_store.attach_work_conversation(work_id, row.conversation_id)
        role_id = _request_role_id(request)
        role = self.role_store.get_role(role_id) if role_id else None
        if role is not None and role.current_conversation_id is None:
            self.role_store.attach_conversation(role.role_id, row.conversation_id)


def _looks_like_execution(envelope: BridgeEnvelope) -> bool:
    return "execution_id" in envelope.body or "attempt_id" in envelope.body


def _request_role_id(request: dict[str, Any]) -> str | None:
    parameters = request.get("parameters")
    if isinstance(parameters, dict) and isinstance(parameters.get("role_id"), str):
        return parameters["role_id"]
    extensions = request.get("extensions")
    if not isinstance(extensions, dict):
        return None
    workflow = extensions.get("agent_bridge.workflow")
    if isinstance(workflow, dict) and isinstance(workflow.get("role_id"), str):
        return workflow["role_id"]
    coordinator = extensions.get("agent_bridge.coordinator")
    if isinstance(coordinator, dict) and isinstance(coordinator.get("role_id"), str):
        return coordinator["role_id"]
    return None


def _execution_title(instruction: object) -> str:
    if not isinstance(instruction, str) or not instruction.strip():
        return "Agent Bridge Codex execution"
    first_line = instruction.strip().splitlines()[0]
    return first_line[:157] + "…" if len(first_line) > 160 else first_line
