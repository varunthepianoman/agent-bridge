from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from agent_bridge_protocol.models import (
    ArtifactRef,
    DeliveryPolicy,
    EndpointRef,
    ExecutionOperation,
    MessageKind,
)

from .manual_bridge import ManualBridgeService

router = APIRouter(prefix="/api/v1/bridge", tags=["manual bridge"])


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManualEnvelopeInput(_Input):
    kind: MessageKind
    destination: EndpointRef
    body: dict[str, Any] = Field(min_length=1)
    correlation_id: str | None = None
    causation_id: str | None = None
    reply_to: EndpointRef | None = None
    work_id: str | None = None
    delivery: DeliveryPolicy = Field(default_factory=DeliveryPolicy)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)


class ManualMessageSubmit(_Input):
    envelope: ManualEnvelopeInput
    subject: str | None = None


class ManualExecutionInput(_Input):
    operation: ExecutionOperation
    instruction: str = Field(min_length=1)
    target: EndpointRef
    work_id: str | None = None
    conversation_id: str | None = None
    cwd: str | None = Field(default=None, min_length=1)
    adapter: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    delivery: DeliveryPolicy = Field(default_factory=DeliveryPolicy)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)


class RequestEnvelopeOptions(_Input):
    reply_to: EndpointRef | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)


class ManualRequestSubmit(_Input):
    request: ManualExecutionInput
    envelope: RequestEnvelopeOptions | None = None
    subject: str | None = None


class CancellationInput(_Input):
    reason: str = Field(default="cancelled by user", min_length=1, max_length=2000)


def _service(request: Request) -> ManualBridgeService:
    return request.app.state.manual_bridge_service  # type: ignore[no-any-return]


@router.post("/messages", status_code=201)
async def submit_message(payload: ManualMessageSubmit, request: Request) -> dict[str, Any]:
    try:
        return await _service(request).submit_message(
            envelope_input=payload.envelope.model_dump(exclude_none=True),
            custom_subject=payload.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/messages")
def list_messages(
    request: Request,
    status: str | None = None,
    work_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items, total = _service(request).list_messages(
        status=status, work_id=work_id, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/messages/{message_id}")
def get_message(message_id: str, request: Request) -> dict[str, Any]:
    message = _service(request).get_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="manual Bridge message not found")
    return message


@router.post("/requests", status_code=201)
async def submit_request(payload: ManualRequestSubmit, request: Request) -> dict[str, Any]:
    try:
        return await _service(request).submit_request(
            request_input=payload.request.model_dump(exclude_none=True),
            envelope_options=(
                payload.envelope.model_dump(exclude_none=True) if payload.envelope else None
            ),
            custom_subject=payload.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/requests")
def list_requests(
    request: Request,
    status: str | None = None,
    work_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items, total = _service(request).list_executions(
        status=status, work_id=work_id, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/requests/{execution_id}")
def get_request(execution_id: str, request: Request) -> dict[str, Any]:
    return _get_execution(_service(request), execution_id)


@router.get("/executions")
def list_executions(
    request: Request,
    status: str | None = None,
    work_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items, total = _service(request).list_executions(
        status=status, work_id=work_id, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/executions/{execution_id}")
def get_execution(execution_id: str, request: Request) -> dict[str, Any]:
    return _get_execution(_service(request), execution_id)


@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(
    execution_id: str, payload: CancellationInput, request: Request
) -> dict[str, Any]:
    try:
        return await _service(request).cancel_execution(execution_id, reason=payload.reason)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _get_execution(service: ManualBridgeService, execution_id: str) -> dict[str, Any]:
    execution = service.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="execution not found")
    return execution


def mount_manual_bridge_api(app: FastAPI) -> None:
    app.include_router(router)
