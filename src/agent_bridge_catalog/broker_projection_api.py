from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request

from .broker_projection import BrokerProjectionStore

router = APIRouter(prefix="/api/v1/bridge/operations", tags=["bridge operations"])


def _store(request: Request) -> BrokerProjectionStore:
    return request.app.state.broker_projection_store  # type: ignore[no-any-return]


@router.get("/messages")
def list_messages(
    request: Request,
    state: str | None = None,
    stream: str | None = None,
    correlation_id: str | None = None,
    work_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items, total = _store(request).list_messages(
        state=state,
        stream=stream,
        correlation_id=correlation_id,
        work_id=work_id,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/messages/{message_id}")
def get_message(message_id: str, request: Request) -> dict[str, Any]:
    item = _store(request).get_message(message_id)
    if item is None:
        raise HTTPException(status_code=404, detail="broker message not found")
    return item


@router.get("/deliveries")
def list_deliveries(
    request: Request,
    state: str | None = None,
    consumer: str | None = None,
    message_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items, total = _store(request).list_deliveries(
        state=state,
        consumer=consumer,
        message_id=message_id,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/dead-letters")
def list_dead_letters(
    request: Request,
    unresolved_only: bool = True,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items, total = _store(request).list_dead_letters(
        unresolved_only=unresolved_only, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/consumers")
def list_consumers(request: Request, stream: str | None = None) -> dict[str, Any]:
    items = _store(request).list_consumers(stream=stream)
    return {"items": items, "total": len(items)}


@router.get("/summary")
def broker_summary(request: Request) -> dict[str, Any]:
    return _store(request).summary()


def mount_broker_projection_api(app: FastAPI) -> None:
    app.include_router(router)
