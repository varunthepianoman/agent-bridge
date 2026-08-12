"""REST surface for durable, topology-flexible collaboration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from agent_bridge_protocol.models import (
    CollaborationOperation,
    CollaborationRoom,
    EndpointRef,
    RegisteredEndpoint,
)

from .collaboration import CollaborationService, CollaborationStore, topology
from .roles import RoleStore

router = APIRouter(prefix="/api/v1/collaboration", tags=["collaboration"])


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EndpointCreate(_Input):
    endpoint_id: str | None = None
    display_name: str = Field(min_length=1, max_length=500)
    address: EndpointRef
    capabilities: list[str] = Field(default_factory=list)
    work_ids: list[str] = Field(default_factory=list)
    status: str = Field(default="active", min_length=1, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)


class EndpointUpdate(_Input):
    display_name: str | None = Field(default=None, min_length=1, max_length=500)
    address: EndpointRef | None = None
    capabilities: list[str] | None = None
    work_ids: list[str] | None = None
    status: str | None = Field(default=None, min_length=1, max_length=80)
    metadata: dict[str, Any] | None = None
    extensions: dict[str, Any] | None = None


class RoomCreate(_Input):
    room_id: str | None = None
    name: str = Field(min_length=1, max_length=500)
    work_id: str | None = None
    durable: bool = True
    members: list[EndpointRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)


class RoomUpdate(_Input):
    name: str | None = Field(default=None, min_length=1, max_length=500)
    work_id: str | None = None
    durable: bool | None = None
    members: list[EndpointRef] | None = None
    metadata: dict[str, Any] | None = None
    extensions: dict[str, Any] | None = None


class CollaborationSubmit(_Input):
    operation: CollaborationOperation
    body: dict[str, Any] = Field(min_length=1)
    destinations: list[EndpointRef] = Field(default_factory=list)
    capability: str | None = None
    room_id: str | None = None
    work_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    reply_to: EndpointRef | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)


def _store(request: Request) -> CollaborationStore:
    return request.app.state.collaboration_store  # type: ignore[no-any-return]


def _service(request: Request) -> CollaborationService:
    return request.app.state.collaboration_service  # type: ignore[no-any-return]


def _roles(request: Request) -> RoleStore:
    return request.app.state.role_store  # type: ignore[no-any-return]


def _error(exc: Exception) -> HTTPException:
    message = str(exc).strip("'")
    if "unknown" in message or "not found" in message:
        return HTTPException(status_code=404, detail=message)
    if "already exists" in message or "conflict" in message:
        return HTTPException(status_code=409, detail=message)
    return HTTPException(status_code=422, detail=message)


@router.post("/endpoints", response_model=RegisteredEndpoint, status_code=status.HTTP_201_CREATED)
def create_endpoint(payload: EndpointCreate, request: Request) -> RegisteredEndpoint:
    now = datetime.now(UTC)
    item = RegisteredEndpoint(
        **payload.model_dump(exclude={"endpoint_id"}),
        endpoint_id=payload.endpoint_id or f"endpoint-{uuid4().hex}",
        created_at=now,
        updated_at=now,
    )
    try:
        return _store(request).register_endpoint(item)
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/endpoints")
def list_endpoints(
    request: Request,
    capability: str | None = None,
    work_id: str | None = None,
    endpoint_status: str | None = Query(default=None, alias="status"),
) -> dict[str, Any]:
    items = _store(request).list_endpoints(
        capability=capability, work_id=work_id, status=endpoint_status
    )
    return {"items": items, "total": len(items)}


@router.get("/endpoints/{endpoint_id}", response_model=RegisteredEndpoint)
def get_endpoint(endpoint_id: str, request: Request) -> RegisteredEndpoint:
    item = _store(request).get_endpoint(endpoint_id)
    if item is None:
        raise HTTPException(status_code=404, detail="endpoint not found")
    return item


@router.patch("/endpoints/{endpoint_id}", response_model=RegisteredEndpoint)
def update_endpoint(
    endpoint_id: str, payload: EndpointUpdate, request: Request
) -> RegisteredEndpoint:
    try:
        return _store(request).update_endpoint(endpoint_id, payload.model_dump(exclude_unset=True))
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/rooms", response_model=CollaborationRoom, status_code=status.HTTP_201_CREATED)
def create_room(payload: RoomCreate, request: Request) -> CollaborationRoom:
    now = datetime.now(UTC)
    item = CollaborationRoom(
        **payload.model_dump(exclude={"room_id"}),
        room_id=payload.room_id or f"room-{uuid4().hex}",
        created_at=now,
        updated_at=now,
    )
    try:
        return _store(request).create_room(item)
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/rooms")
def list_rooms(request: Request, work_id: str | None = None) -> dict[str, Any]:
    items = _store(request).list_rooms(work_id=work_id)
    return {"items": items, "total": len(items)}


@router.get("/rooms/{room_id}", response_model=CollaborationRoom)
def get_room(room_id: str, request: Request) -> CollaborationRoom:
    item = _store(request).get_room(room_id)
    if item is None:
        raise HTTPException(status_code=404, detail="room not found")
    return item


@router.patch("/rooms/{room_id}", response_model=CollaborationRoom)
def update_room(room_id: str, payload: RoomUpdate, request: Request) -> CollaborationRoom:
    try:
        return _store(request).update_room(room_id, payload.model_dump(exclude_unset=True))
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/messages", status_code=status.HTTP_201_CREATED)
async def submit_message(payload: CollaborationSubmit, request: Request) -> Any:
    try:
        service = _service(request)
        return await service.submit(
            operation=payload.operation,
            sender=service.bridge.sender,
            body=payload.body,
            destinations=payload.destinations,
            capability=payload.capability,
            room_id=payload.room_id,
            work_id=payload.work_id,
            correlation_id=payload.correlation_id,
            causation_id=payload.causation_id,
            reply_to=payload.reply_to,
            extensions=payload.extensions,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/messages")
def list_messages(
    request: Request,
    work_id: str | None = None,
    correlation_id: str | None = None,
    operation: CollaborationOperation | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items, total = _store(request).list_messages(
        work_id=work_id,
        correlation_id=correlation_id,
        operation=str(operation) if operation else None,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/messages/{collaboration_id}")
def get_message(collaboration_id: str, request: Request) -> Any:
    item = _store(request).get_message(collaboration_id)
    if item is None:
        raise HTTPException(status_code=404, detail="collaboration message not found")
    return item


@router.get("/topology")
def get_topology(request: Request) -> dict[str, Any]:
    return topology(_store(request), _roles(request))


@router.get("/native-subagents")
def list_native_subagents(request: Request) -> dict[str, Any]:
    items = _store(request).list_native_subagents()
    return {"items": items, "total": len(items)}


def mount_collaboration_api(app: FastAPI) -> None:
    app.include_router(router)
