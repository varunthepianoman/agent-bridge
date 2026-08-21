"""HTTP API for the conversation directory, messaging, attention, and diagnostics."""

from __future__ import annotations

import json
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from .core import AttentionStore, CollectionStore, MessageStore, NatsEventStore, RoomStore
from .repository import CatalogRepository

router = APIRouter(prefix="/api/v1")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateImport(Input):
    conversation_ids: list[str] = Field(min_length=1)


class ConversationPatch(Input):
    alias: str | None = Field(default=None, min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=20_000)
    tags: list[str] | None = None
    pinned: bool | None = None
    hidden: bool | None = None
    archived: bool | None = None


class ConversationCreate(Input):
    provider: str = Field(pattern="^(codex|claude)$")
    cwd: str = Field(min_length=1)
    initial_prompt: str = Field(min_length=1)
    alias: str | None = Field(default=None, min_length=1, max_length=500)
    node_id: str | None = None
    environment_id: str | None = None
    model: str | None = Field(default=None, min_length=1, max_length=160)
    effort: Literal["low", "medium", "high", "xhigh", "max", "ultra"] | None = None


class TurnCreate(Input):
    prompt: str = Field(min_length=1)
    effort: Literal["low", "medium", "high", "xhigh", "max", "ultra"] | None = None


class CatalogSettingsPatch(Input):
    auto_add_new_chats: bool


class CollectionCreate(Input):
    name: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=5_000)
    kind: str = Field(default="manual", pattern="^(manual|smart)$")
    filters: dict[str, Any] = Field(default_factory=dict)


class CollectionUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5_000)
    filters: dict[str, Any] | None = None


class RoomCreate(Input):
    name: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=5_000)


class RoomUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5_000)


class RoomMembership(Input):
    delivery_mode: str = Field(pattern="^(wake|notify|digest)$")


class MessageCreate(Input):
    body: str = Field(min_length=1, max_length=200_000)
    target_conversation_id: str | None = None
    room_id: str | None = None
    source_conversation_id: str | None = None
    actor_kind: str = Field(default="human", pattern="^(human|agent)$")
    operation: str = Field(
        default="message",
        pattern="^(message|request|reply|forward|complete|needs_user)$",
    )
    correlation_id: str | None = None
    causation_id: str | None = None
    delivery_strategy: str = Field(
        default="queue", pattern="^(queue|steer-or-queue)$"
    )


def _repository(request: Request) -> CatalogRepository:
    return request.app.state.repository  # type: ignore[no-any-return]


def _collections(request: Request) -> CollectionStore:
    return request.app.state.collections  # type: ignore[no-any-return]


def _rooms(request: Request) -> RoomStore:
    return request.app.state.rooms  # type: ignore[no-any-return]


def _messages(request: Request) -> MessageStore:
    return request.app.state.messages  # type: ignore[no-any-return]


def _attention(request: Request) -> AttentionStore:
    return request.app.state.attention  # type: ignore[no-any-return]


def _nats(request: Request) -> NatsEventStore:
    return request.app.state.nats_events  # type: ignore[no-any-return]


def _conversation(request: Request, conversation_id: str) -> Any:
    row = _repository(request).get(conversation_id)
    if row is None or not row.selected:
        raise HTTPException(status_code=404, detail="conversation not found")
    return row


def _conversation_dict(request: Request, row: Any, **kwargs: Any) -> dict[str, Any]:
    return cast(dict[str, Any], request.app.state.conversation_dict(row, **kwargs))


@router.get("/health")
def health(request: Request, response: Response) -> dict[str, Any]:
    transport = request.app.state.transport
    broker_configured = transport is not None
    broker_connected = getattr(transport, "connected", True) if transport is not None else None
    degraded = broker_configured and not broker_connected
    if degraded:
        response.status_code = 503
    return {
        "status": "degraded" if degraded else "ok",
        "broker_configured": broker_configured,
        "broker_connected": broker_connected,
        "background": request.app.state.supervisor.snapshot()["status"],
    }


@router.get("/settings")
def get_settings(request: Request) -> dict[str, bool]:
    return request.app.state.preferences.as_dict()  # type: ignore[no-any-return]


@router.patch("/settings")
def update_settings(payload: CatalogSettingsPatch, request: Request) -> dict[str, bool]:
    request.app.state.preferences.set_auto_add_new_chats(payload.auto_add_new_chats)
    return request.app.state.preferences.as_dict()  # type: ignore[no-any-return]


@router.get("/conversations")
def conversations(
    request: Request,
    q: str | None = None,
    provider: str | None = None,
    source: str | None = None,
    conversation_status: str | None = Query(default=None, alias="status"),
    node_id: str | None = None,
    environment_id: str | None = None,
    conversation_kind: str | None = None,
    delivery_mode: str | None = None,
    archived: bool | None = None,
    pinned: bool | None = None,
    include_hidden: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    rows, total = _repository(request).list(
        query=q,
        provider=provider,
        source=source,
        status=conversation_status,
        node_id=node_id,
        environment_id=environment_id,
        conversation_kind=conversation_kind,
        delivery_mode=delivery_mode,
        archived=archived,
        pinned=pinned,
        include_hidden=include_hidden,
        limit=limit,
        offset=offset,
    )
    return {"items": [request.app.state.conversation_dict(row) for row in rows], "total": total}


@router.get("/conversations/candidates")
def candidates(
    request: Request, node_id: str | None = None, environment_id: str | None = None
) -> dict[str, Any]:
    rows = _repository(request).candidates(node_id=node_id, environment_id=environment_id)
    return {"items": [request.app.state.conversation_dict(row) for row in rows], "total": len(rows)}


@router.post("/conversations/import")
def import_candidates(payload: CandidateImport, request: Request) -> dict[str, Any]:
    try:
        rows = _repository(request).select(payload.conversation_ids)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"items": [request.app.state.conversation_dict(row) for row in rows], "total": len(rows)}


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(payload: ConversationCreate, request: Request) -> dict[str, Any]:
    node_id = payload.node_id or request.app.state.settings.node_id
    environment_id = payload.environment_id or request.app.state.settings.environment_id
    if node_id != request.app.state.settings.node_id:
        try:
            command = request.app.state.node_store.queue_command(
                node_id=node_id,
                kind="start_conversation",
                payload={
                    "provider": payload.provider,
                    "workspace": payload.cwd,
                    "environment_id": environment_id,
                    "prompt": payload.initial_prompt,
                    "alias": payload.alias,
                    "model": payload.model,
                    "effort": payload.effort,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "queued": True,
            "command_id": command["command_id"],
            "node_id": node_id,
            "environment_id": environment_id,
        }
    try:
        thread_id = await request.app.state.runtime.start(
            provider=payload.provider,
            cwd=payload.cwd,
            prompt=payload.initial_prompt,
            model=payload.model,
            effort=payload.effort,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row = _repository(request).upsert_discovered(
        {
            "provider": payload.provider,
            "provider_thread_id": thread_id,
            "title": payload.alias or payload.initial_prompt.splitlines()[0][:96],
            "preview": payload.initial_prompt,
            "cwd": payload.cwd,
            "status": "active",
            "source_kind": "agent_bridge",
            "raw_metadata": {
                "launch_model": payload.model,
                "launch_effort": payload.effort,
            },
        },
        node_id=node_id,
        environment_id=environment_id,
    )
    row = _repository(request).select([row.conversation_id])[0]
    if payload.alias:
        updated = _repository(request).update_metadata(
            row.conversation_id, {"alias": payload.alias}
        )
        assert updated is not None
        row = updated
    return _conversation_dict(request, row)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
    return _conversation_dict(
        request, _conversation(request, conversation_id), include_transcript=True
    )


@router.patch("/conversations/{conversation_id}")
def update_conversation(
    conversation_id: str, payload: ConversationPatch, request: Request
) -> dict[str, Any]:
    _conversation(request, conversation_id)
    row = _repository(request).update_metadata(
        conversation_id, payload.model_dump(exclude_unset=True)
    )
    assert row is not None
    return _conversation_dict(request, row, include_transcript=True)


@router.delete("/conversations/{conversation_id}", status_code=204)
def remove_conversation(conversation_id: str, request: Request) -> Response:
    if not _repository(request).deselect(conversation_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/turns", status_code=202)
async def create_turn(
    conversation_id: str, payload: TurnCreate, request: Request
) -> dict[str, Any]:
    row = _conversation(request, conversation_id)
    if row.node_id != request.app.state.settings.node_id:
        try:
            command = request.app.state.node_store.queue_command(
                node_id=row.node_id,
                kind="deliver_turn",
                conversation_id=row.conversation_id,
                payload={
                    "provider": row.provider,
                    "provider_thread_id": row.provider_thread_id,
                    "workspace": row.cwd or ".",
                    "environment_id": row.environment_id,
                    "prompt": payload.prompt,
                    "effort": payload.effort,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "status": "queued_remote",
            "conversation_id": conversation_id,
            "command_id": command["command_id"],
        }

    async def run() -> None:
        try:
            await request.app.state.runtime.turn(
                provider=row.provider,
                provider_thread_id=row.provider_thread_id,
                cwd=row.cwd or ".",
                prompt=payload.prompt,
                effort=payload.effort,
            )
            _attention(request).create(
                category="update",
                kind="turn_completed",
                title=f"Chat {row.conversation_number} finished a turn",
                conversation_id=row.conversation_id,
            )
        except Exception as exc:
            _attention(request).create(
                category="needs_attention",
                kind="turn_failed",
                title=f"Chat {row.conversation_number} turn failed",
                detail=str(exc),
                conversation_id=row.conversation_id,
            )

    request.app.state.supervisor.create_task(
        run(),
        name=f"turn-{conversation_id}-{uuid4().hex}",
        critical=False,
    )
    return {"status": "queued", "conversation_id": conversation_id}


@router.post("/conversations/{conversation_id}/open")
def open_conversation(
    conversation_id: str,
    request: Request,
    target: str = Query(default="terminal", pattern="^(desktop|terminal)$"),
) -> dict[str, Any]:
    row = _conversation(request, conversation_id)
    if target == "desktop":
        native_url = request.app.state.conversation_dict(row).get("native_url")
        if not native_url:
            raise HTTPException(
                status_code=409, detail="conversation has no local desktop-app locator"
            )
        result = request.app.state.launcher.open_url(native_url, requested=True)
        return {
            "queued": False,
            "launched": result.launched,
            "command": result.command,
            "detail": result.detail,
        }
    if not row.resume_command:
        raise HTTPException(status_code=409, detail="conversation has no native resume locator")
    if row.node_id != request.app.state.settings.node_id:
        try:
            command = request.app.state.node_store.queue_command(
                node_id=row.node_id,
                kind="resume_conversation",
                conversation_id=row.conversation_id,
                payload={
                    "provider": row.provider,
                    "provider_thread_id": row.provider_thread_id,
                    "workspace": row.cwd,
                    "environment_id": row.environment_id,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"queued": True, "command_id": command["command_id"]}
    result = request.app.state.launcher.launch(row.resume_command, requested=True)
    return {
        "queued": False,
        "launched": result.launched,
        "command": result.command,
        "detail": result.detail,
    }


@router.delete("/conversations/{conversation_id}/transcript")
def delete_transcript(conversation_id: str, request: Request) -> dict[str, Any]:
    if not request.app.state.maintenance.delete_transcript(conversation_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"conversation_id": conversation_id, "transcript_deleted": True}


@router.post("/reconciliation")
async def reconcile(request: Request) -> dict[str, int]:
    result = await request.app.state.synchronizer.reconcile(include_turns=True)
    return {"discovered": result.discovered, "imported": result.imported}


@router.get("/collections")
def list_collections(request: Request) -> dict[str, Any]:
    items = _collections(request).list()
    return {"items": items, "total": len(items)}


@router.post("/collections", status_code=201)
def create_collection(payload: CollectionCreate, request: Request) -> dict[str, Any]:
    return _collections(request).create(**payload.model_dump())


@router.patch("/collections/{collection_id}")
def update_collection(
    collection_id: str, payload: CollectionUpdate, request: Request
) -> dict[str, Any]:
    item = _collections(request).update(collection_id, payload.model_dump(exclude_unset=True))
    if item is None:
        raise HTTPException(status_code=404, detail="collection not found")
    return item


@router.delete("/collections/{collection_id}", status_code=204)
def delete_collection(collection_id: str, request: Request) -> Response:
    if not _collections(request).delete(collection_id):
        raise HTTPException(status_code=404, detail="collection not found")
    return Response(status_code=204)


@router.put("/collections/{collection_id}/members/{conversation_id}")
def add_collection_member(
    collection_id: str, conversation_id: str, request: Request
) -> dict[str, bool]:
    if not _collections(request).set_member(collection_id, conversation_id, present=True):
        raise HTTPException(status_code=404, detail="collection or conversation not found")
    return {"added": True}


@router.delete("/collections/{collection_id}/members/{conversation_id}")
def remove_collection_member(
    collection_id: str, conversation_id: str, request: Request
) -> dict[str, bool]:
    if not _collections(request).set_member(collection_id, conversation_id, present=False):
        raise HTTPException(status_code=404, detail="collection or conversation not found")
    return {"removed": True}


@router.get("/rooms")
def list_rooms(request: Request) -> dict[str, Any]:
    items = _rooms(request).list()
    return {"items": items, "total": len(items)}


@router.post("/rooms", status_code=201)
def create_room(payload: RoomCreate, request: Request) -> dict[str, Any]:
    return _rooms(request).create(**payload.model_dump())


@router.patch("/rooms/{room_id}")
def update_room(room_id: str, payload: RoomUpdate, request: Request) -> dict[str, Any]:
    item = _rooms(request).update(room_id, payload.model_dump(exclude_unset=True))
    if item is None:
        raise HTTPException(status_code=404, detail="room not found")
    return item


@router.delete("/rooms/{room_id}", status_code=204)
def delete_room(room_id: str, request: Request) -> Response:
    if not _rooms(request).delete(room_id):
        raise HTTPException(status_code=404, detail="room not found")
    return Response(status_code=204)


@router.put("/rooms/{room_id}/members/{conversation_id}")
def add_room_member(
    room_id: str, conversation_id: str, payload: RoomMembership, request: Request
) -> dict[str, bool]:
    if not _rooms(request).set_member(room_id, conversation_id, mode=payload.delivery_mode):
        raise HTTPException(status_code=404, detail="room or conversation not found")
    return {"added": True}


@router.delete("/rooms/{room_id}/members/{conversation_id}")
def remove_room_member(room_id: str, conversation_id: str, request: Request) -> dict[str, bool]:
    if not _rooms(request).set_member(room_id, conversation_id, mode=None):
        raise HTTPException(status_code=404, detail="room or conversation not found")
    return {"removed": True}


@router.get("/messages")
def list_messages(request: Request, correlation_id: str | None = None) -> dict[str, Any]:
    items = _messages(request).list(correlation_id=correlation_id)
    return {"items": items, "total": len(items)}


@router.post("/messages", status_code=201)
async def send_message(payload: MessageCreate, request: Request) -> dict[str, Any]:
    try:
        return await _messages(request).send(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/messages/{message_id}")
def get_message(message_id: str, request: Request) -> dict[str, Any]:
    item = _messages(request).get(message_id)
    if item is None:
        raise HTTPException(status_code=404, detail="message not found")
    return item


@router.get("/correlations/{correlation_id}")
def get_correlation(correlation_id: str, request: Request) -> dict[str, Any]:
    return _messages(request).correlation(correlation_id)


@router.get("/attention")
def list_attention(
    request: Request, category: str | None = None, unread_only: bool = False
) -> dict[str, Any]:
    items = _attention(request).list(category=category, unread_only=unread_only)
    return {"items": items, "total": len(items)}


@router.post("/attention/{attention_id}/acknowledge")
def acknowledge(attention_id: str, request: Request) -> dict[str, bool]:
    if not _attention(request).acknowledge(attention_id):
        raise HTTPException(status_code=404, detail="attention item not found")
    return {"acknowledged": True}


@router.post("/attention/acknowledge-all")
def acknowledge_all(request: Request) -> dict[str, int]:
    return {"acknowledged": _attention(request).acknowledge_all()}


@router.get("/nats/summary")
async def nats_summary(request: Request) -> dict[str, Any]:
    result = _nats(request).summary()
    transport = request.app.state.transport
    diagnostics = getattr(transport, "diagnostics", None)
    result["broker"] = (
        await diagnostics()
        if diagnostics is not None
        else {"status": "injected" if transport else "not_configured"}
    )
    return result


@router.get("/nats/activity")
def nats_activity(
    request: Request, limit: int = Query(default=200, ge=1, le=2_000)
) -> dict[str, Any]:
    items = _nats(request).list(limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/nats/issues")
def nats_issues(
    request: Request, limit: int = Query(default=200, ge=1, le=2_000)
) -> dict[str, Any]:
    items = [
        item
        for item in _nats(request).list(limit=limit)
        if item["severity"] in {"warning", "error"}
    ]
    return {"items": items, "total": len(items)}


@router.get("/nats/deliveries")
def nats_deliveries(
    request: Request, limit: int = Query(default=200, ge=1, le=2_000)
) -> dict[str, Any]:
    items = _nats(request).list(category="delivery", limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/nats/dead-letters")
def dead_letters(
    request: Request, limit: int = Query(default=200, ge=1, le=2_000)
) -> dict[str, Any]:
    items, total = request.app.state.broker_projection.list_dead_letters(limit=limit, offset=0)
    return {"items": items, "total": total}


@router.get("/nats/export")
def export_nats(request: Request) -> Response:
    return Response(
        json.dumps(_nats(request).list(limit=2_000), indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=agent-bridge-nats-events.json"},
    )


def mount_core_api(app: FastAPI) -> None:
    app.include_router(router)
