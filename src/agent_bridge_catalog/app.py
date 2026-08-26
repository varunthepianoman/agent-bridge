from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from agent_bridge_bridge.logging_context import bind_log_context
from agent_bridge_bridge.observer import BrokerActivity
from agent_bridge_bridge.transport import JetStreamSettings, JetStreamTransport

from .broker_observer import BrokerProjectionObserver
from .broker_projection import BrokerProjectionStore
from .config import Settings
from .core import (
    AttentionStore,
    CollectionStore,
    MailboxStore,
    MessageStore,
    NatsEventStore,
    RoomStore,
)
from .core_api import mount_core_api
from .db import ConversationRow, Database
from .delivery import ConversationDeliveryWorker
from .launcher import NativeLauncher
from .maintenance import MaintenanceService
from .node_api import mount_node_api
from .nodes import NodeStore
from .preferences import PreferenceStore
from .repository import CatalogRepository
from .runtime import ConversationRuntime
from .supervision import BackgroundSupervisor
from .sync import CatalogSynchronizer, ConversationProvider


class _UnavailableProvider:
    async def discover(self, *, include_turns: bool = True) -> AsyncIterator[Any]:
        del include_turns
        raise RuntimeError("no conversation provider was configured")
        yield


class _ActivityObserver:
    def __init__(self, projection: BrokerProjectionObserver, events: NatsEventStore) -> None:
        self.projection = projection
        self.events = events

    async def record(self, activity: BrokerActivity) -> None:
        await self.projection.record(activity)
        await asyncio.to_thread(
            self.events.record,
            category=("delivery" if str(activity.kind) != "published" else "activity"),
            direction=("outbound" if str(activity.kind) == "published" else "inbound"),
            severity=("error" if str(activity.kind) == "dead_lettered" else "info"),
            subject=activity.subject,
            message_id=activity.message_id,
            correlation_id=activity.correlation_id,
            occurred_at=activity.occurred_at,
            detail={"kind": str(activity.kind), **activity.detail},
        )


def create_app(
    *,
    settings: Settings | None = None,
    provider: ConversationProvider | None = None,
    database: Database | None = None,
    bridge_publisher: Any | None = None,
    **_legacy: Any,
) -> FastAPI:
    resolved = settings or Settings.from_environment()
    resolved.state_dir.mkdir(parents=True, exist_ok=True)
    db = database or Database(resolved.database_url)
    repository = CatalogRepository(db)
    preferences = PreferenceStore(db)
    maintenance = MaintenanceService(db)
    nodes = NodeStore(db, repository, preferences.auto_add_new_chats)
    broker_projection = BrokerProjectionStore(db)
    attention = AttentionStore(db)
    collections = CollectionStore(db)
    rooms = RoomStore(db)
    nats_events = NatsEventStore(db)
    supervisor = BackgroundSupervisor()
    observer = _ActivityObserver(BrokerProjectionObserver(broker_projection), nats_events)
    transport: Any | None = bridge_publisher
    managed_transport: JetStreamTransport | None = None
    if transport is None and resolved.nats_servers:
        managed_transport = JetStreamTransport(
            JetStreamSettings(
                servers=resolved.nats_servers,
                client_name=resolved.nats_client_name,
                credentials_file=resolved.nats_credentials_file,
                username=resolved.nats_username,
                password=resolved.nats_password,
                replicas=resolved.nats_replicas,
            ),
            observer=observer,
        )
        transport = managed_transport
    messages = MessageStore(db, transport, attention)
    mailbox = MailboxStore(db)
    launcher = NativeLauncher()
    runtime = ConversationRuntime(
        codex_bin=resolved.codex_bin,
        claude_bin=resolved.claude_bin,
    )
    delivery_worker = ConversationDeliveryWorker(
        repository=repository,
        messages=messages,
        mailbox=mailbox,
        rooms=rooms,
        attention=attention,
    )

    selected_provider: ConversationProvider | None = provider

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal selected_provider
        db.initialize()
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
                        ClaudeCatalogAdapter(claude_bin=resolved.claude_bin),
                    ]
                )
            except ImportError:
                selected_provider = _UnavailableProvider()
        app.state.synchronizer = CatalogSynchronizer(
            repository,
            selected_provider,
            node_id=resolved.node_id,
            environment_id=resolved.environment_id,
            auto_add_new_chats=preferences.auto_add_new_chats,
        )
        try:
            if managed_transport is not None:
                try:
                    await managed_transport.connect()
                    await managed_transport.provision_streams()
                    conversation_subscription = await managed_transport.subscribe(
                        "bridge.v1.inbox.conversation.*",
                        durable_name="agent-bridge-conversations-v1",
                    )
                    room_subscription = await managed_transport.subscribe(
                        "bridge.v1.room.*",
                        durable_name="agent-bridge-rooms-v1",
                    )
                    supervisor.create_task(
                        delivery_worker.serve(conversation_subscription),
                        name="conversation-message-delivery",
                        critical=True,
                    )
                    supervisor.create_task(
                        delivery_worker.serve(room_subscription),
                        name="room-message-delivery",
                        critical=True,
                    )
                except Exception as exc:
                    nats_events.record(
                        category="issue",
                        severity="error",
                        detail={"code": "broker_startup_failed", "message": str(exc)},
                    )

            async def reconcile_forever() -> None:
                while True:
                    try:
                        await app.state.synchronizer.reconcile(include_turns=False)
                    except Exception as exc:
                        nats_events.record(
                            category="issue",
                            severity="warning",
                            detail={"code": "reconciliation_failed", "message": str(exc)},
                        )
                    await asyncio.sleep(resolved.discovery_interval_seconds)

            async def full_reconcile_forever() -> None:
                while True:
                    await asyncio.sleep(resolved.full_reconciliation_interval_seconds)
                    try:
                        await app.state.synchronizer.reconcile(include_turns=True)
                    except Exception as exc:
                        nats_events.record(
                            category="issue",
                            severity="warning",
                            detail={"code": "full_reconciliation_failed", "message": str(exc)},
                        )

            async def mailbox_outcome_sweep_forever() -> None:
                while True:
                    await asyncio.sleep(resolved.mailbox_sweep_interval_seconds)
                    try:
                        stale = await asyncio.to_thread(
                            mailbox.claim_stale_received,
                            older_than=datetime.now(UTC)
                            - timedelta(seconds=resolved.mailbox_outcome_grace_seconds),
                        )
                        for item in stale:
                            attention.create(
                                category="needs_attention",
                                kind="mailbox_outcome_missing",
                                title="Mailbox message needs an outcome",
                                detail=(
                                    f"Message {item['message_id']} was received but has no "
                                    "succeeded, blocked, or failed outcome. Requeue it manually "
                                    "if another listener should process it."
                                ),
                                conversation_id=item["recipient_conversation_id"],
                                correlation_id=item["correlation_id"],
                            )
                    except Exception as exc:
                        nats_events.record(
                            category="issue",
                            severity="warning",
                            detail={"code": "mailbox_outcome_sweep_failed", "message": str(exc)},
                        )

            supervisor.create_task(
                reconcile_forever(),
                name="conversation-reconciliation",
                critical=False,
            )
            supervisor.create_task(
                full_reconcile_forever(),
                name="full-conversation-reconciliation",
                critical=False,
            )
            supervisor.create_task(
                mailbox_outcome_sweep_forever(),
                name="mailbox-outcome-sweep",
                critical=False,
            )
            yield
        finally:
            await supervisor.stop()
            await runtime.close()
            if managed_transport is not None:
                await managed_transport.close()
            close = getattr(selected_provider, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result

    app = FastAPI(title="Agent Bridge", version="0.3.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Correlation-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        correlation_id = request.headers.get("X-Correlation-ID") or f"http-{uuid4().hex}"
        with bind_log_context(correlation_id=correlation_id):
            response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response

    def conversation_dict(
        row: ConversationRow, *, include_transcript: bool = False
    ) -> dict[str, Any]:
        result = row.as_dict(include_transcript=include_transcript)
        result.update(nodes.location_status(row.node_id, row.environment_id))
        result["display_name"] = (
            f"Chat {row.conversation_number} · {row.alias}"
            if row.conversation_number is not None
            else row.alias
        )
        result["capabilities"] = {
            "can_open": bool(row.resume_command),
            "can_receive_turn": row.delivery_mode == "direct",
            "can_message": True,
        }
        native_url = None
        if row.provider.casefold() == "codex":
            native_url = f"codex://threads/{quote(row.provider_thread_id, safe='')}"
        elif row.node_id == resolved.node_id and row.provider.casefold() == "claude":
            folder = quote(row.cwd or ".", safe="")
            native_url = f"claude://code/new?folder={folder}"
        result["native_url"] = native_url
        result["native_launch_enabled"] = True
        return result

    app.state.database = db
    app.state.settings = resolved
    app.state.repository = repository
    app.state.preferences = preferences
    app.state.maintenance = maintenance
    app.state.node_store = nodes
    app.state.broker_projection = broker_projection
    app.state.attention = attention
    app.state.collections = collections
    app.state.rooms = rooms
    app.state.messages = messages
    app.state.mailbox = mailbox
    app.state.nats_events = nats_events
    app.state.supervisor = supervisor
    app.state.transport = transport
    app.state.runtime = runtime
    app.state.delivery_worker = delivery_worker
    app.state.launcher = launcher
    app.state.conversation_dict = conversation_dict
    mount_core_api(app)
    mount_node_api(app)
    return app


app = create_app()
