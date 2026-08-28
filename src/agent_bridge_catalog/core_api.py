"""HTTP API for the conversation directory, messaging, attention, and diagnostics."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from .core import (
    AttentionStore,
    CollectionStore,
    MailboxStore,
    MessageStore,
    NatsEventStore,
    RoomStore,
)
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


class SteerCreate(Input):
    prompt: str = Field(min_length=1)


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
    delivery_mode: str = Field(pattern="^(mailbox|notify|digest)$")


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
    acknowledgement_requested: bool = False


class MailboxWait(Input):
    max_wait_seconds: float = Field(default=3600, ge=0, le=3600)
    batch_limit: int = Field(default=50, ge=1, le=50)


class MessageComplete(Input):
    conversation_id: str = Field(min_length=1)
    outcome: Literal["succeeded", "blocked", "failed"]
    detail: str | None = Field(default=None, max_length=20_000)
    reply_body: str | None = Field(default=None, min_length=1, max_length=200_000)


class MessageRequeue(Input):
    conversation_id: str = Field(min_length=1)
    detail: str | None = Field(default=None, max_length=20_000)


class MessageAcknowledge(Input):
    conversation_id: str = Field(min_length=1)
    detail: str | None = Field(default=None, max_length=20_000)


class ReceiptWait(Input):
    source_conversation_id: str = Field(min_length=1)
    until: Literal["claimed", "acknowledged", "terminal"] = "acknowledged"
    timeout_seconds: float = Field(default=3600, ge=0, le=3600)
    after_revision: int | None = Field(default=None, ge=0)


def _repository(request: Request) -> CatalogRepository:
    return request.app.state.repository  # type: ignore[no-any-return]


def _collections(request: Request) -> CollectionStore:
    return request.app.state.collections  # type: ignore[no-any-return]


def _rooms(request: Request) -> RoomStore:
    return request.app.state.rooms  # type: ignore[no-any-return]


def _messages(request: Request) -> MessageStore:
    return request.app.state.messages  # type: ignore[no-any-return]


def _mailbox(request: Request) -> MailboxStore:
    return request.app.state.mailbox  # type: ignore[no-any-return]


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


def _message_dict(request: Request, item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    deliveries = _mailbox(request).list_message_deliveries(item["message_id"])
    result["processing_deliveries"] = deliveries
    if len(deliveries) == 1:
        for key in (
            "processing_state",
            "processing_detail",
            "outcome_detail",
            "received_at",
            "claimed_at",
            "acknowledged_at",
            "acknowledgement_detail",
            "attempt",
            "revision",
            "completed_at",
            "outcome_at",
            "reply_message_id",
        ):
            result[key] = deliveries[0].get(key)
    return result


def _receipt_reached(
    delivery: dict[str, Any] | None,
    *,
    milestone: str,
    after_revision: int | None,
) -> bool:
    if delivery is None:
        return False
    revision = int(delivery.get("revision") or 0)
    if after_revision is not None and revision <= after_revision:
        return False
    if milestone == "claimed":
        return delivery.get("claimed_at") is not None or delivery.get("received_at") is not None
    if milestone == "acknowledged":
        return delivery.get("acknowledged_at") is not None
    return delivery.get("state") in {"succeeded", "blocked", "failed"}


def _receipt_snapshot(
    request: Request,
    message: dict[str, Any],
    *,
    status_value: Literal["reached", "timeout"],
    waited_for: str,
) -> dict[str, Any]:
    target_id = message.get("target_conversation_id")
    delivery = (
        _mailbox(request).get_delivery(message["message_id"], str(target_id))
        if target_id
        else None
    )
    target = _repository(request).get(str(target_id)) if target_id else None
    target_projection = _conversation_dict(request, target) if target is not None else {}
    return {
        "status": status_value,
        "waited_for": waited_for,
        "message": _message_dict(request, message),
        "receipt": delivery,
        "recipient_listener": _mailbox(request).get_listener(str(target_id))
        if target_id
        else None,
        "recipient_node_reachable": target_projection.get("node_reachable"),
    }


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


@router.post("/conversations/{conversation_id}/refresh")
async def refresh_conversation(
    conversation_id: str,
    request: Request,
    response: Response,
    wait_seconds: float = Query(default=0, ge=0, le=60),
) -> dict[str, Any]:
    """Request a read-only projection refresh from the conversation's owning node."""

    row = _conversation(request, conversation_id)
    if row.provider.casefold() != "codex":
        raise HTTPException(status_code=409, detail="targeted refresh currently supports Codex")
    if row.node_id == request.app.state.settings.node_id:
        raise HTTPException(
            status_code=409,
            detail="targeted refresh is only required for remote conversations",
        )
    try:
        command = request.app.state.node_store.queue_command(
            node_id=row.node_id,
            kind="read_conversation",
            conversation_id=row.conversation_id,
            payload={
                "provider": row.provider,
                "provider_thread_id": row.provider_thread_id,
                "environment_id": row.environment_id,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    deadline = time.monotonic() + wait_seconds
    current = command
    while wait_seconds > 0 and current["status"] in {"queued", "claimed"}:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(0.1, remaining))
        refreshed = request.app.state.node_store.get_command(command["command_id"])
        if refreshed is None:
            raise HTTPException(status_code=404, detail="refresh command not found")
        current = refreshed

    if current["status"] == "succeeded":
        refreshed_row = _conversation(request, conversation_id)
        return {
            "status": "succeeded",
            "command_id": command["command_id"],
            "conversation": _conversation_dict(
                request, refreshed_row, include_transcript=True
            ),
        }
    response.status_code = status.HTTP_202_ACCEPTED
    return {
        "status": current["status"],
        "command_id": command["command_id"],
        "detail": (current.get("result") or {}).get("detail"),
    }


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


@router.post("/conversations/{conversation_id}/steer")
async def steer_active_turn(
    conversation_id: str, payload: SteerCreate, request: Request
) -> dict[str, Any]:
    """Explicitly steer a local active Codex turn, with no queued-turn fallback."""

    row = _conversation(request, conversation_id)
    if row.node_id != request.app.state.settings.node_id:
        raise HTTPException(status_code=409, detail="remote active-turn steering is unsupported")
    steer_id = f"steer-{uuid4().hex}"
    try:
        result = await request.app.state.runtime.deliver_active_turn(
            provider=row.provider,
            provider_thread_id=row.provider_thread_id,
            cwd=row.cwd or ".",
            prompt=payload.prompt,
            message_id=steer_id,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if str(result.state) != "delivered":
        raise HTTPException(
            status_code=409,
            detail={"state": str(result.state), "detail": result.detail},
        )
    return {
        "status": "delivered",
        "conversation_id": conversation_id,
        "steer_id": steer_id,
    }


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
        if row.provider.casefold() == "codex" and row.node_id != request.app.state.settings.node_id:
            try:
                command = request.app.state.node_store.queue_command(
                    node_id=row.node_id,
                    kind="open_native_url",
                    conversation_id=row.conversation_id,
                    payload={
                        "native_url": native_url,
                        "environment_id": row.environment_id,
                    },
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return {"queued": True, "command_id": command["command_id"]}
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
    return {"items": [_message_dict(request, item) for item in items], "total": len(items)}


@router.post("/messages", status_code=201)
async def send_message(payload: MessageCreate, request: Request) -> dict[str, Any]:
    if payload.acknowledgement_requested:
        if payload.room_id is not None or payload.target_conversation_id is None:
            raise HTTPException(
                status_code=422,
                detail="acknowledgement receipts require a direct conversation target",
            )
        if payload.source_conversation_id is None:
            raise HTTPException(
                status_code=422,
                detail="acknowledgement receipts require a source conversation",
            )
        _conversation(request, payload.source_conversation_id)
        _conversation(request, payload.target_conversation_id)
    try:
        return await _messages(request).send(
            **payload.model_dump(),
            delivery_strategy="mailbox",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/messages/{message_id}")
def get_message(message_id: str, request: Request) -> dict[str, Any]:
    item = _messages(request).get(message_id)
    if item is None:
        raise HTTPException(status_code=404, detail="message not found")
    return _message_dict(request, item)


@router.get("/mailbox/{conversation_id}")
def list_inbox(
    conversation_id: str,
    request: Request,
    processing_state: Literal[
        "pending", "claimed", "received", "succeeded", "blocked", "failed"
    ]
    | None = Query(default=None, alias="state"),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    _conversation(request, conversation_id)
    stored_state = "received" if processing_state == "claimed" else processing_state
    items = _mailbox(request).list_inbox(conversation_id, state=stored_state, limit=limit)
    return {
        "items": items,
        "total": len(items),
        "listener": _mailbox(request).get_listener(conversation_id),
    }


@router.post("/mailbox/{conversation_id}/wait")
async def wait_mailbox(
    conversation_id: str, payload: MailboxWait, request: Request
) -> dict[str, Any]:
    """Wait in the caller's foreground turn and atomically receive pending mail."""

    _conversation(request, conversation_id)
    listener_id = f"listener-{uuid4().hex}"
    lease_seconds = 15.0
    try:
        listener = await asyncio.to_thread(
            _mailbox(request).acquire_listener,
            conversation_id,
            listener_id=listener_id,
            lease_seconds=lease_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    fencing_token = int(listener["fencing_token"])
    deadline = time.monotonic() + payload.max_wait_seconds
    try:
        while True:
            current = await asyncio.to_thread(_mailbox(request).get_listener, conversation_id)
            if current is None or current.get("stop_requested_at") is not None:
                return {"status": "stopped", "items": [], "listener": current}
            try:
                items = await asyncio.to_thread(
                    _mailbox(request).receive_pending,
                    conversation_id,
                    listener_id=listener_id,
                    fencing_token=fencing_token,
                    limit=payload.batch_limit,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if items:
                return {"status": "received", "items": items, "listener": current}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"status": "timeout", "items": [], "listener": current}
            await asyncio.sleep(min(0.5, remaining))
            try:
                listener = await asyncio.to_thread(
                    _mailbox(request).heartbeat_listener,
                    conversation_id,
                    listener_id=listener_id,
                    fencing_token=fencing_token,
                    lease_seconds=lease_seconds,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        await asyncio.to_thread(
            _mailbox(request).release_listener,
            conversation_id,
            listener_id=listener_id,
            fencing_token=fencing_token,
        )


@router.post("/mailbox/{conversation_id}/stop-listener")
def stop_listener(conversation_id: str, request: Request) -> dict[str, Any]:
    _conversation(request, conversation_id)
    listener = _mailbox(request).request_listener_stop(conversation_id)
    return {"status": "stop_requested" if listener else "idle", "listener": listener}


@router.post("/messages/{message_id}/acknowledge")
def acknowledge_message(
    message_id: str, payload: MessageAcknowledge, request: Request
) -> dict[str, Any]:
    _conversation(request, payload.conversation_id)
    try:
        return _mailbox(request).acknowledge(
            message_id, payload.conversation_id, detail=payload.detail
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/messages/{message_id}/wait-receipt")
async def wait_for_receipt(
    message_id: str, payload: ReceiptWait, request: Request
) -> dict[str, Any]:
    _conversation(request, payload.source_conversation_id)
    message = _messages(request).get(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="message not found")
    if message.get("room_id") is not None or message.get("target_conversation_id") is None:
        raise HTTPException(status_code=422, detail="receipt waits require a direct message")
    if message.get("source_conversation_id") != payload.source_conversation_id:
        raise HTTPException(status_code=403, detail="source conversation does not own message")

    deadline = time.monotonic() + payload.timeout_seconds
    while True:
        current = _messages(request).get(message_id)
        if current is None:
            raise HTTPException(status_code=404, detail="message not found")
        target_id = str(current["target_conversation_id"])
        delivery = await asyncio.to_thread(
            _mailbox(request).get_delivery, message_id, target_id
        )
        if _receipt_reached(
            delivery,
            milestone=payload.until,
            after_revision=payload.after_revision,
        ):
            return _receipt_snapshot(
                request, current, status_value="reached", waited_for=payload.until
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _receipt_snapshot(
                request, current, status_value="timeout", waited_for=payload.until
            )
        await asyncio.sleep(min(0.5, remaining))


@router.post("/messages/{message_id}/complete")
async def complete_message(
    message_id: str, payload: MessageComplete, request: Request
) -> dict[str, Any]:
    _conversation(request, payload.conversation_id)
    delivery = _mailbox(request).get_delivery(message_id, payload.conversation_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="mailbox delivery not found")
    if delivery.get("listener_id") is None or delivery.get("fencing_token") is None:
        raise HTTPException(status_code=409, detail="message has not been received")

    reply_message_id: str | None = delivery.get("reply_message_id")
    if payload.reply_body is not None:
        source_id = delivery.get("source_conversation_id")
        if not source_id:
            raise HTTPException(status_code=409, detail="message has no conversation reply target")
        digest = hashlib.sha256(
            f"{message_id}\0{payload.conversation_id}".encode()
        ).hexdigest()[:32]
        reply_message_id = f"message-reply-{digest}"
        try:
            await _messages(request).send(
                message_id=reply_message_id,
                body=payload.reply_body,
                target_conversation_id=str(source_id),
                room_id=None,
                source_conversation_id=payload.conversation_id,
                actor_kind="agent",
                operation="reply",
                correlation_id=str(delivery["correlation_id"]),
                causation_id=message_id,
                delivery_strategy="mailbox",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        return _mailbox(request).complete(
            message_id,
            payload.conversation_id,
            outcome=payload.outcome,
            detail=payload.detail,
            listener_id=str(delivery["listener_id"]),
            fencing_token=int(delivery["fencing_token"]),
            reply_message_id=reply_message_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/messages/{message_id}/requeue")
def requeue_message(
    message_id: str, payload: MessageRequeue, request: Request
) -> dict[str, Any]:
    _conversation(request, payload.conversation_id)
    try:
        return _mailbox(request).requeue(
            message_id, payload.conversation_id, detail=payload.detail
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
