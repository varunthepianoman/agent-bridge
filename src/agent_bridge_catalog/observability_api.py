"""Operational overview and lower-level diagnostic HTTP APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response

from .observability import OperationalObservability

overview = APIRouter(prefix="/api/v1/observability", tags=["observability"])
diagnostics = APIRouter(prefix="/api/v1/diagnostics", tags=["diagnostics"])
metrics_api = APIRouter(tags=["observability"])


def _service(request: Request) -> OperationalObservability:
    return request.app.state.observability  # type: ignore[no-any-return]


@overview.get("/summary")
async def summary(request: Request) -> dict[str, Any]:
    return await _service(request).summary()


@overview.get("/broker")
async def broker(request: Request) -> dict[str, Any]:
    return await _service(request).broker()


@overview.get("/advisories")
async def advisories(request: Request) -> dict[str, Any]:
    items = await _service(request).advisories()
    return {"items": items, "total": len(items)}


@overview.get("/pending-requests")
def pending_requests(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return _service(request).pending_requests(limit=limit, offset=offset)


@overview.get("/executions")
def executions(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return _service(request).executions(limit=limit, offset=offset)


@overview.get("/retries")
def retries(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return _service(request).retries(limit=limit, offset=offset)


@overview.get("/leases")
def leases(request: Request) -> dict[str, Any]:
    return _service(request).leases()


@overview.get("/dead-letters")
def dead_letters(
    request: Request,
    unresolved_only: bool = True,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items, total = _service(request).broker_projection.list_dead_letters(
        unresolved_only=unresolved_only,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@overview.get("/artifacts")
def artifacts(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return _service(request).artifacts(limit=limit, offset=offset)


@overview.get("/roles")
def roles(request: Request) -> dict[str, Any]:
    return _service(request).roles_view()


@overview.get("/nodes")
def nodes(request: Request) -> dict[str, Any]:
    return _service(request).nodes_view()


@diagnostics.get("/background")
def background(request: Request) -> dict[str, Any]:
    return _service(request).supervisor.snapshot()


@diagnostics.get("/broker")
async def raw_broker(request: Request) -> dict[str, Any]:
    return await _service(request).broker()


@diagnostics.get("/messages")
def raw_messages(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items, total = _service(request).broker_projection.list_messages(limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@diagnostics.get("/deliveries")
def raw_deliveries(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items, total = _service(request).broker_projection.list_deliveries(limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@overview.get("/metrics")
@metrics_api.get("/metrics")
async def prometheus(request: Request) -> Response:
    if not request.app.state.settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Prometheus export is disabled")
    return Response(
        await _service(request).prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


def mount_observability_api(app: FastAPI) -> None:
    app.include_router(overview)
    app.include_router(diagnostics)
    app.include_router(metrics_api)
