from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from agent_bridge_bridge.collaboration_worker import CollaborationProjectionWorker
from agent_bridge_bridge.logging_context import bind_log_context
from agent_bridge_bridge.transport import JetStreamSettings, JetStreamTransport
from agent_bridge_coordinator.codex import CodexCoordinatorModel
from agent_bridge_coordinator.engine import (
    CoordinatorActionExecutor,
    CoordinatorEngine,
    CoordinatorModel,
)

from .broker_observer import BrokerProjectionObserver
from .broker_projection import BrokerProjectionStore
from .broker_projection_api import mount_broker_projection_api
from .collaboration import (
    AsyncCollaborationEnvelopeSink,
    CollaborationService,
    CollaborationStore,
)
from .collaboration_api import mount_collaboration_api
from .config import Settings
from .convergence import ConvergenceController
from .convergence_api import mount_convergence_api
from .coordinator_adapter import AsyncCoordinatorPersistence
from .coordinator_api import mount_coordinator_api
from .coordinator_runtime import BridgeCoordinatorActionExecutor, CoordinatorRuntime
from .coordinator_store import CoordinatorStore
from .db import ConversationRow, Database
from .launcher import NativeLauncher
from .maintenance import MaintenanceService
from .manual_bridge import BridgePublisher, ManualBridgeService
from .manual_bridge_api import mount_manual_bridge_api
from .node_api import mount_node_api
from .nodes import NodeStore
from .observability import OperationalObservability, TelemetryExporter
from .observability_api import mount_observability_api
from .repository import CatalogRepository
from .result_projection_worker import ExecutionResultProjectionWorker
from .role_api import mount_role_api
from .roles import RoleStore
from .schemas import ConversationMetadataUpdate, ResumeRequest, SyncRequest
from .supervision import BackgroundSupervisor
from .sync import CatalogSynchronizer, ConversationProvider


class _UnavailableProvider:
    async def discover(self, *, include_turns: bool = True) -> AsyncIterator[Any]:
        raise RuntimeError("no conversation provider was configured")
        yield


def create_app(
    *,
    settings: Settings | None = None,
    provider: ConversationProvider | None = None,
    database: Database | None = None,
    bridge_publisher: BridgePublisher | None = None,
    manual_bridge_service: ManualBridgeService | None = None,
    coordinator_model: CoordinatorModel | None = None,
    coordinator_executor: CoordinatorActionExecutor | None = None,
    telemetry_exporters: tuple[TelemetryExporter, ...] = (),
) -> FastAPI:
    resolved = settings or Settings.from_environment()
    resolved.state_dir.mkdir(parents=True, exist_ok=True)
    db = database or Database(resolved.database_url)
    repository = CatalogRepository(db)
    role_store = RoleStore(db)
    coordinator_store = CoordinatorStore(db, role_store)
    collaboration_store = CollaborationStore(db)
    maintenance_service = MaintenanceService(db)
    node_store = NodeStore(db, repository)
    broker_projection_store = BrokerProjectionStore(db)
    supervisor = BackgroundSupervisor()
    projection_observer = BrokerProjectionObserver(broker_projection_store)
    managed_transport: JetStreamTransport | None = None
    if manual_bridge_service is None and bridge_publisher is None and resolved.nats_servers:
        managed_transport = JetStreamTransport(
            JetStreamSettings(
                servers=resolved.nats_servers,
                client_name=resolved.nats_client_name,
                credentials_file=resolved.nats_credentials_file,
                username=resolved.nats_username,
                password=resolved.nats_password,
            ),
            observer=projection_observer,
        )
    resolved_manual_bridge_service = manual_bridge_service or ManualBridgeService(
        db,
        publisher=bridge_publisher or cast(BridgePublisher | None, managed_transport),
        observer=projection_observer if managed_transport is None else None,
    )
    collaboration_sink = AsyncCollaborationEnvelopeSink(collaboration_store)
    convergence_controller = ConvergenceController(
        resolved_manual_bridge_service, role_store, repository
    )
    result_projection_worker = ExecutionResultProjectionWorker(
        resolved_manual_bridge_service,
        collaboration_sink,
        repository=repository,
        role_store=role_store,
        node_store=node_store,
        environment_id=resolved.environment_id,
        convergence=convergence_controller,
    )
    collaboration_service = CollaborationService(
        collaboration_store, resolved_manual_bridge_service
    )
    collaboration_projection_worker = CollaborationProjectionWorker(collaboration_sink)
    coordinator_engine: CoordinatorEngine | None = None
    coordinator_unavailable_reason: str | None = None
    sdk_available = importlib.util.find_spec("openai_codex") is not None
    if resolved.coordinator_enabled and coordinator_model is None and not sdk_available:
        coordinator_unavailable_reason = (
            "Codex coordinator SDK is unavailable; install the 'codex' optional dependency"
        )
    if coordinator_model is not None or (
        resolved.coordinator_enabled and coordinator_unavailable_reason is None
    ):
        selected_coordinator_model = coordinator_model or CodexCoordinatorModel(
            model=resolved.coordinator_model,
            default_cwd=resolved.coordinator_workspace,
        )
        coordinator_engine = CoordinatorEngine(
            store=AsyncCoordinatorPersistence(
                coordinator_store,
                repository,
                node_id=resolved.node_id,
                environment_id=resolved.environment_id,
            ),
            model=selected_coordinator_model,
            executor=coordinator_executor
            or BridgeCoordinatorActionExecutor(resolved_manual_bridge_service),
            holder_id=resolved.coordinator_holder_id,
            lease_seconds=resolved.coordinator_lease_seconds,
        )
    coordinator_runtime = CoordinatorRuntime(
        store=coordinator_store,
        engine=coordinator_engine,
        unavailable_reason=(
            coordinator_unavailable_reason
            or (
                None
                if coordinator_engine is not None
                else "coordinator runtime is disabled; set AGENT_BRIDGE_COORDINATOR_ENABLED=1"
            )
        ),
    )
    observability = OperationalObservability(
        database=db,
        broker_projection=broker_projection_store,
        nodes=node_store,
        roles=role_store,
        coordinator=coordinator_runtime,
        supervisor=supervisor,
        live_broker=managed_transport,
        exporters=telemetry_exporters,
    )
    launcher = NativeLauncher(enabled=resolved.native_launch_enabled)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.initialize()
        selected_provider: ConversationProvider | None = provider
        if selected_provider is None:
            try:
                from agent_bridge_providers import (
                    ClaudeCatalogAdapter,
                    CodexCatalogAdapter,
                    CompositeCatalogAdapter,
                )

                selected_provider = CompositeCatalogAdapter(
                    [
                        CodexCatalogAdapter(codex_bin=resolved.codex_bin),
                        ClaudeCatalogAdapter(),
                    ]
                )
            except ImportError:
                selected_provider = _UnavailableProvider()
        app.state.synchronizer = CatalogSynchronizer(
            repository,
            selected_provider,
            node_id=resolved.node_id,
            environment_id=resolved.environment_id,
        )
        try:
            if managed_transport is not None:
                await managed_transport.connect()
                await managed_transport.provision_streams()
                result_subscription = await result_projection_worker.subscribe(
                    managed_transport,
                    durable_name=resolved.result_consumer_durable,
                )
                supervisor.create_task(
                    result_projection_worker.run_forever(result_subscription),
                    name="agent-bridge-result-projection",
                )

                async def project_collaboration(subscription: Any) -> None:
                    while True:
                        handled = await collaboration_projection_worker.run_once(
                            subscription, batch=50, timeout=1.0
                        )
                        if not handled:
                            await asyncio.sleep(0.1)

                for family in ("inbox", "capability", "room", "event"):
                    collaboration_subscription = await managed_transport.subscribe(
                        f"bridge.v1.{family}.>",
                        durable_name=f"catalog-collaboration-{family}-v1",
                        ack_wait_seconds=60.0,
                    )
                    supervisor.create_task(
                        project_collaboration(collaboration_subscription),
                        name=f"agent-bridge-collaboration-{family}-projection",
                    )
            # Recovered coordinator work may publish immediately, so Bridge transport must
            # be ready before intake reconciliation starts.
            await coordinator_runtime.start()
            if telemetry_exporters:

                async def export_telemetry() -> None:
                    while True:
                        await observability.export()
                        await asyncio.sleep(resolved.telemetry_interval_seconds)

                supervisor.create_task(
                    export_telemetry(),
                    name="agent-bridge-telemetry-export",
                    critical=False,
                )
            yield
        finally:
            await coordinator_runtime.stop()
            await supervisor.stop()
            if managed_transport is not None:
                await managed_transport.close()
            close = getattr(selected_provider, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result

    app = FastAPI(title="AI Work Catalog", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Correlation-ID"],
    )

    @app.middleware("http")
    async def structured_request_context(request: Request, call_next: Any) -> Any:
        correlation_id = request.headers.get("X-Correlation-ID") or f"http-{uuid4().hex}"
        with bind_log_context(
            correlation_id=correlation_id,
            work_id=request.query_params.get("work_id"),
        ):
            response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response

    app.state.database = db
    app.state.settings = resolved
    app.state.repository = repository
    app.state.role_store = role_store
    app.state.coordinator_store = coordinator_store
    app.state.collaboration_store = collaboration_store
    app.state.maintenance_service = maintenance_service
    app.state.collaboration_service = collaboration_service
    app.state.collaboration_projection_worker = collaboration_projection_worker
    app.state.coordinator_runtime = coordinator_runtime
    app.state.node_store = node_store
    app.state.broker_projection_store = broker_projection_store
    app.state.observability = observability
    app.state.background_supervisor = supervisor
    app.state.manual_bridge_service = resolved_manual_bridge_service
    app.state.bridge_transport = managed_transport
    app.state.result_projection_worker = result_projection_worker
    app.state.convergence_controller = convergence_controller
    mount_role_api(app)
    mount_coordinator_api(app)
    mount_collaboration_api(app)
    mount_node_api(app)
    mount_observability_api(app)
    mount_broker_projection_api(app)
    mount_manual_bridge_api(app)
    mount_convergence_api(app)

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    local_thread_cache: tuple[float, frozenset[str]] = (0.0, frozenset())

    def local_codex_thread_ids() -> frozenset[str]:
        nonlocal local_thread_cache
        now = time.monotonic()
        if now - local_thread_cache[0] < 5.0:
            return local_thread_cache[1]
        thread_ids: set[str] = set()
        for root in (codex_home / "sessions", codex_home / "archived_sessions"):
            if not root.is_dir():
                continue
            for path in root.rglob("rollout-*.jsonl"):
                candidate = path.stem[-36:]
                try:
                    thread_ids.add(str(UUID(candidate)))
                except ValueError:
                    continue
        local_thread_cache = (now, frozenset(thread_ids))
        return local_thread_cache[1]

    def conversation_dict(
        row: ConversationRow, *, include_transcript: bool = False
    ) -> dict[str, Any]:
        result = row.as_dict(include_transcript=include_transcript)
        result.update(node_store.location_status(row.node_id, row.environment_id))
        result["interactive_open"] = interactive_open_dict(row)
        return result

    def interactive_open_dict(row: ConversationRow) -> dict[str, Any]:
        terminal = {
            "available": bool(row.resume_command),
            "command": row.resume_command,
        }
        if row.provider.casefold() != "codex":
            return {
                "desktop": {
                    "available": False,
                    "reason": "Desktop deep links are currently supported only for Codex chats.",
                },
                "terminal": terminal,
            }

        metadata = json.loads(row.raw_metadata_json or "{}")
        rollout_path = metadata.get("path") if isinstance(metadata, dict) else None
        local_rollout_exists = bool(
            isinstance(rollout_path, str)
            and Path(rollout_path).is_absolute()
            and Path(rollout_path).is_file()
        )
        local_rollout_exists = (
            local_rollout_exists or row.provider_thread_id in local_codex_thread_ids()
        )
        local_owner = row.node_id == resolved.node_id
        if local_rollout_exists or local_owner:
            return {
                "desktop": {
                    "available": True,
                    "url": f"codex://threads/{quote(row.provider_thread_id, safe='')}",
                    "reason": None,
                },
                "terminal": terminal,
            }
        return {
            "desktop": {
                "available": False,
                "reason": (
                    "This Codex thread is not present in this machine's local history. "
                    "Open it from its owning host or use that host's terminal resume command."
                ),
            },
            "terminal": terminal,
        }

    @app.get("/api/v1/health")
    def health(response: Response) -> dict[str, Any]:
        broker_connected = managed_transport.connected if managed_transport else None
        broker_configured = bool(resolved.nats_servers)
        degraded = supervisor.degraded or (
            resolved.broker_required and (not broker_configured or not broker_connected)
        ) or (managed_transport is not None and not broker_connected)
        if degraded:
            response.status_code = 503
        return {
            "status": "degraded" if degraded else "ok",
            "broker_required": resolved.broker_required,
            "broker_configured": broker_configured,
            "broker_connected": broker_connected,
            "background": supervisor.snapshot()["status"],
        }

    @app.get("/api/v1/conversations")
    def list_conversations(
        q: str | None = None,
        provider_name: str | None = Query(default=None, alias="provider"),
        source: str | None = None,
        status: str | None = None,
        archived: bool | None = None,
        pinned: bool | None = None,
        include_hidden: bool = False,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        try:
            rows, total = repository.list(
                query=q,
                provider=provider_name,
                source=source,
                status=status,
                archived=archived,
                pinned=pinned,
                include_hidden=include_hidden,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid search: {exc}") from exc
        return {
            "items": [conversation_dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/v1/search")
    def search(
        q: str = Query(min_length=1), limit: int = Query(default=100, ge=1, le=500)
    ) -> dict[str, Any]:
        rows, total = repository.list(query=q, limit=limit)
        return {
            "items": [conversation_dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": 0,
        }

    @app.get("/api/v1/conversations/{conversation_id}")
    def get_conversation(conversation_id: str) -> dict[str, Any]:
        row = repository.get(conversation_id)
        if row is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return conversation_dict(row, include_transcript=True)

    @app.patch("/api/v1/conversations/{conversation_id}")
    def update_conversation(
        conversation_id: str, update: ConversationMetadataUpdate
    ) -> dict[str, Any]:
        try:
            row = repository.update_metadata(conversation_id, update.changes())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return conversation_dict(row, include_transcript=True)

    @app.delete("/api/v1/conversations/{conversation_id}/transcript")
    def delete_conversation_transcript(conversation_id: str) -> dict[str, Any]:
        if not maintenance_service.delete_transcript(conversation_id):
            raise HTTPException(status_code=404, detail="conversation not found")
        row = repository.get(conversation_id)
        assert row is not None
        return {
            "conversation_id": conversation_id,
            "transcript_deleted": True,
            "conversation": conversation_dict(row, include_transcript=True),
        }

    @app.post("/api/v1/actions/sync")
    async def sync_catalog(payload: SyncRequest, request: Request) -> dict[str, int]:
        try:
            result = await request.app.state.synchronizer.reconcile(
                include_turns=payload.include_turns
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"provider synchronization failed: {exc}"
            ) from exc
        return {"discovered": result.discovered, "imported": result.imported}

    @app.post("/api/v1/actions/resume")
    def resume_conversation(payload: ResumeRequest) -> dict[str, Any]:
        row = repository.get(payload.conversation_id)
        if row is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        if not row.resume_command:
            raise HTTPException(status_code=409, detail="conversation has no native resume locator")
        if row.node_id != resolved.node_id:
            if not node_store.is_reachable(row.node_id):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "owning environment is unavailable; no fallback was attempted",
                        "command": row.resume_command,
                        "node_id": row.node_id,
                        "environment_id": row.environment_id,
                    },
                )
            command = node_store.queue_command(
                node_id=row.node_id,
                kind="resume_conversation",
                conversation_id=row.conversation_id,
                payload={
                    "provider": row.provider,
                    "provider_thread_id": row.provider_thread_id,
                    "workspace": row.cwd,
                    "environment_id": row.environment_id,
                    "resume_command": row.resume_command,
                },
            )
            return {
                "command": row.resume_command,
                "launched": False,
                "queued": True,
                "command_id": command["command_id"],
                "detail": "resume queued on the owning node",
            }
        result = launcher.launch(row.resume_command, requested=payload.launch)
        return {
            "command": result.command,
            "launched": result.launched,
            "queued": False,
            "detail": result.detail,
        }

    return app


app = create_app()
