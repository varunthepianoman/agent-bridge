"""Production coordinator runtime and its authorized Bridge action executor."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from agent_bridge_coordinator.engine import CoordinatorEngine
from agent_bridge_coordinator.models import (
    BudgetUsage,
    CoordinatorAction,
    CoordinatorActionType,
)
from agent_bridge_bridge.subjects import capability_subject
from agent_bridge_protocol.models import (
    CoordinatorIntakeStatus,
    CoordinatorRole,
    EndpointKind,
    EndpointRef,
    ExecutionOperation,
    WorkRequest,
)

from .coordinator_store import CoordinatorStore
from .db import BridgeExecutionRow, ManualBridgeMessageRow
from .manual_bridge import ManualBridgeService

PORTFOLIO_ROLE_ID = "role-portfolio-coordinator"


class CoordinatorRuntimeUnavailable(RuntimeError):
    pass


class _ActionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: ExecutionOperation = ExecutionOperation.INVOKE_ADAPTER
    instruction: str | None = Field(default=None, min_length=1)
    target: EndpointRef | None = None
    conversation_id: str | None = None
    adapter: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)
    work_id: str | None = None
    repository_id: str | None = None
    path: str | None = None


class BridgeCoordinatorActionExecutor:
    """Translate already-authorized execution actions into durable Bridge requests."""

    _SUPPORTED = {
        CoordinatorActionType.EXECUTE,
        CoordinatorActionType.DELEGATE,
        CoordinatorActionType.RETRY,
    }

    def __init__(self, bridge: ManualBridgeService) -> None:
        self.bridge = bridge

    async def execute(
        self,
        action: CoordinatorAction,
        *,
        request: WorkRequest,
        role: CoordinatorRole,
    ) -> BudgetUsage:
        if action.type not in self._SUPPORTED:
            raise ValueError(f"coordinator action is not implemented: {action.type}")
        if not action.capability:
            raise ValueError("Bridge execution action requires a capability")
        payload = _ActionPayload.model_validate(action.payload)
        if not action.target_id:
            raise ValueError("Bridge execution action requires a target")
        if payload.target is None:
            if action.target_id != action.capability:
                raise ValueError("non-capability target requires an explicit typed payload target")
            target = EndpointRef(kind=EndpointKind.CAPABILITY, id=action.target_id)
        else:
            target = payload.target
        if target.id != action.target_id:
            raise ValueError("action payload target does not match authorized target_id")
        if (
            payload.operation == ExecutionOperation.RESUME_CONVERSATION
            and not payload.conversation_id
        ):
            raise ValueError("resume_conversation requires conversation_id")
        adapter = payload.adapter
        if payload.operation == ExecutionOperation.INVOKE_ADAPTER:
            adapter = adapter or action.capability
            if adapter != action.capability:
                raise ValueError("action adapter does not match authorized capability")

        dispatch_key = f"{request.request_id}:{role.role_id}:{action.action_id}"
        dispatch_state = await asyncio.to_thread(self._dispatch_state, dispatch_key)
        if dispatch_state == "published":
            return BudgetUsage()
        if dispatch_state is not None:
            raise RuntimeError(
                f"existing coordinator Bridge dispatch is not confirmed: {dispatch_state}"
            )
        result = await self.bridge.submit_request(
            request_input={
                "operation": payload.operation,
                "instruction": payload.instruction or action.summary,
                "target": target,
                "work_id": payload.work_id or request.work_id,
                "conversation_id": payload.conversation_id,
                "adapter": adapter,
                "parameters": {
                    **payload.parameters,
                    **({"repository_id": payload.repository_id} if payload.repository_id else {}),
                    **({"path": payload.path} if payload.path else {}),
                },
                "artifacts": request.artifacts,
                "extensions": {
                    **payload.extensions,
                    "agent_bridge.coordinator": {
                        "dispatch_key": dispatch_key,
                        "action_id": action.action_id,
                        "role_id": role.role_id,
                        "request_id": request.request_id,
                        "action_type": str(action.type),
                    },
                },
            },
            # The execution target preserves the logical role/node identity, while
            # adapter work is physically routed to a runner advertising the authorized
            # capability. Without this override, role-targeted adapter requests wait on
            # a role inbox that capability runners do not consume.
            custom_subject=capability_subject(action.capability),
        )
        message = result["message"]
        if message.get("status") != "published":
            raise RuntimeError(
                f"Bridge dispatch failed: {message.get('error') or message.get('status')}"
            )
        # The engine reserved the action's attempt budget before dispatch. Publishing the
        # durable request does not itself consume a worker execution attempt.
        return BudgetUsage()

    def _dispatch_state(self, dispatch_key: str) -> str | None:
        """Use the authoritative persisted request as a durable idempotency record."""
        with self.bridge.database.session() as session:
            rows = session.execute(
                select(BridgeExecutionRow.request_json, ManualBridgeMessageRow.status).join(
                    ManualBridgeMessageRow,
                    ManualBridgeMessageRow.message_id == BridgeExecutionRow.request_message_id,
                )
            )
            for raw_request, status in rows:
                request = json.loads(raw_request)
                marker = request.get("extensions", {}).get("agent_bridge.coordinator", {})
                if marker.get("dispatch_key") == dispatch_key:
                    return str(status)
        return None


class CoordinatorRuntime:
    """Runs durable intake in background tasks and recovers queued work on startup."""

    def __init__(
        self,
        *,
        store: CoordinatorStore,
        engine: CoordinatorEngine | None,
        unavailable_reason: str | None = None,
    ) -> None:
        self.store = store
        self.engine = engine
        self.unavailable_reason = unavailable_reason or (
            None if engine is not None else "coordinator runtime is disabled"
        )
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._reconciler: asyncio.Task[None] | None = None

    @property
    def available(self) -> bool:
        return self.engine is not None

    def status(self) -> dict[str, Any]:
        return {
            "status": "available" if self.available else "unavailable",
            "reason": self.unavailable_reason,
            "active_intake_ids": sorted(self._tasks),
            "portfolio_role_id": PORTFOLIO_ROLE_ID,
        }

    async def start(self) -> None:
        await asyncio.to_thread(self.store.bootstrap_portfolio_role)
        if not self.available:
            return
        await self._reconcile()
        self._reconciler = asyncio.create_task(
            self._reconcile_loop(), name="coordinator-intake-reconciler"
        )

    async def _reconcile(self) -> None:
        await asyncio.to_thread(self.store.expire_stale_activations)
        active_activations = await asyncio.to_thread(self.store.list_activations, status="active")
        live_intake_ids = {
            item.intake_request_id for item in active_activations if item.intake_request_id
        }
        active_intakes, _ = await asyncio.to_thread(
            self.store.list_intakes,
            status=str(CoordinatorIntakeStatus.ACTIVE),
            limit=500,
            offset=0,
        )
        for intake in active_intakes:
            if intake.request_id not in live_intake_ids:
                await asyncio.to_thread(
                    self.store.update_intake,
                    intake.request_id,
                    status=CoordinatorIntakeStatus.SUBMITTED,
                )
        for status in (
            CoordinatorIntakeStatus.SUBMITTED,
            CoordinatorIntakeStatus.APPROVED,
        ):
            intakes, _ = await asyncio.to_thread(
                self.store.list_intakes, status=str(status), limit=500, offset=0
            )
            for intake in intakes:
                self.schedule(intake.request_id)

    async def _reconcile_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            await self._reconcile()

    async def stop(self) -> None:
        if self._reconciler is not None:
            self._reconciler.cancel()
            await asyncio.gather(self._reconciler, return_exceptions=True)
            self._reconciler = None
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def require_available(self) -> None:
        if not self.available:
            raise CoordinatorRuntimeUnavailable(
                self.unavailable_reason or "coordinator runtime is unavailable"
            )

    def schedule(self, request_id: str, *, role_id: str | None = None) -> None:
        self.require_available()
        incumbent = self._tasks.get(request_id)
        if incumbent is not None and not incumbent.done():
            return
        task = asyncio.create_task(
            self._process(request_id, role_id=role_id),
            name=f"coordinator-intake-{request_id}",
        )
        self._tasks[request_id] = task
        task.add_done_callback(lambda completed: self._task_done(request_id, completed))

    async def process_now(self, request_id: str, *, role_id: str | None = None) -> None:
        self.require_available()
        await self._process(request_id, role_id=role_id)

    async def _process(self, request_id: str, *, role_id: str | None) -> None:
        assert self.engine is not None
        intake = await asyncio.to_thread(self.store.get_intake, request_id)
        if intake is None:
            raise LookupError(f"unknown intake: {request_id}")
        selected_role = (
            role_id or intake.routed_role_id or intake.request.target_role_id or PORTFOLIO_ROLE_ID
        )
        try:
            await self.engine.activate(
                selected_role,
                intake.request,
                intake_id=intake.request_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            latest = await asyncio.to_thread(self.store.get_intake, request_id)
            if latest is not None and latest.status not in {
                CoordinatorIntakeStatus.FAILED,
                CoordinatorIntakeStatus.COMPLETED,
                CoordinatorIntakeStatus.REJECTED,
            }:
                await asyncio.to_thread(
                    self.store.update_intake,
                    request_id,
                    status=CoordinatorIntakeStatus.FAILED,
                    attention_required=str(error),
                )
            raise

    def _task_done(self, request_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(request_id) is task:
            self._tasks.pop(request_id, None)
        # Retrieve the exception so background failures never become noisy, unobserved tasks.
        if not task.cancelled():
            task.exception()
