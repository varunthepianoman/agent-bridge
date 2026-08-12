"""HTTP API for work organization and durable logical roles."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response, status

from agent_bridge_protocol.models import (
    CoordinatorRole,
    EndpointKind,
    Relationship,
    RoleCheckpoint,
    RoleReport,
    WorkItem,
)

from .schemas import (
    CoordinatorRoleCreate,
    CoordinatorRoleUpdate,
    RelationshipCreate,
    RoleCheckpointCreate,
    RoleConversationHandoff,
    RoleLeaseReleaseRequest,
    RoleLeaseRenewRequest,
    RoleLeaseRequest,
    RoleReportCreate,
    WorkConversationCreate,
    WorkItemCreate,
    WorkItemUpdate,
)


class RoleStore(Protocol):
    """Structural contract used by the router and the SQL-backed role store."""

    def create_work(self, item: WorkItem) -> WorkItem: ...

    def list_work(
        self, *, status: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[WorkItem]: ...

    def count_work(self, *, status: str | None = None) -> int: ...

    def get_work(self, work_id: str) -> WorkItem | None: ...

    def update_work(self, work_id: str, changes: dict[str, Any]) -> WorkItem | None: ...

    def create_relationship(self, relationship: Relationship) -> Relationship: ...

    def list_relationships(self, **filters: Any) -> list[Relationship]: ...

    def delete_relationship(self, relationship_id: str) -> bool: ...

    def attach_work_conversation(
        self, work_id: str, conversation_id: str, *, relationship_id: str | None = None
    ) -> Relationship: ...

    def detach_work_conversation(self, work_id: str, conversation_id: str) -> bool: ...

    def create_role(self, role: CoordinatorRole) -> CoordinatorRole: ...

    def list_roles(self, **filters: Any) -> list[CoordinatorRole]: ...

    def get_role(self, role_id: str) -> CoordinatorRole | None: ...

    def update_role(self, role_id: str, changes: dict[str, Any]) -> CoordinatorRole | None: ...

    def append_checkpoint(self, checkpoint: RoleCheckpoint) -> RoleCheckpoint: ...

    def list_checkpoints(self, role_id: str) -> list[RoleCheckpoint]: ...

    def append_report(self, report: RoleReport) -> RoleReport: ...

    def list_reports(self, role_id: str, **filters: Any) -> list[RoleReport]: ...

    def list_events(self, role_id: str) -> list[Any]: ...

    def attach_conversation(
        self, role_id: str, conversation_id: str, handoff_summary: str | None = None
    ) -> Any: ...

    def rotate_conversation(
        self, role_id: str, new_id: str, handoff_summary: str | None = None
    ) -> Any: ...

    def list_role_conversations(self, role_id: str) -> list[Any]: ...

    def generate_handoff(self, role_id: str) -> Any: ...

    def acquire_role_lease(self, role_id: str, holder_id: str, ttl_seconds: float) -> Any: ...

    def renew_role_lease(
        self, role_id: str, holder_id: str, fencing_token: int, ttl_seconds: float
    ) -> Any: ...

    def release_role_lease(self, role_id: str, holder_id: str, fencing_token: int) -> Any: ...


router = APIRouter(prefix="/api/v1", tags=["work organization"])


def _store(request: Request) -> RoleStore:
    store = getattr(request.app.state, "role_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="role store is not configured")
    return cast(RoleStore, store)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _handle_store_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc).strip("'"))
    if isinstance(exc, (ValueError, RuntimeError)):
        message = str(exc)
        lowered = message.lower()
        if any(word in lowered for word in ("unknown", "not found")):
            code = 404
        elif any(word in lowered for word in ("conflict", "stale", "lease", "already exists")):
            code = 409
        else:
            code = 422
        return HTTPException(status_code=code, detail=message)
    return HTTPException(status_code=500, detail="role store operation failed")


@router.get("/work-items")
def list_work_items(
    request: Request, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)
) -> dict[str, Any]:
    store = _store(request)
    page = store.list_work(limit=limit, offset=offset)
    return {
        "items": page,
        "total": store.count_work(),
        "limit": limit,
        "offset": offset,
    }


@router.post("/work-items", status_code=status.HTTP_201_CREATED)
def create_work_item(payload: WorkItemCreate, request: Request) -> WorkItem:
    now = datetime.now(UTC)
    item = WorkItem(
        **payload.model_dump(exclude={"work_id"}),
        work_id=payload.work_id or _id("work"),
        created_at=now,
        updated_at=now,
    )
    try:
        return _store(request).create_work(item)
    except Exception as exc:
        raise _handle_store_error(exc) from exc


@router.get("/work-items/{work_id}")
def get_work_item(work_id: str, request: Request) -> WorkItem:
    item = _store(request).get_work(work_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work item not found")
    return item


@router.patch("/work-items/{work_id}")
def update_work_item(work_id: str, payload: WorkItemUpdate, request: Request) -> WorkItem:
    changes = payload.changes()
    changes["updated_at"] = datetime.now(UTC)
    try:
        item = _store(request).update_work(work_id, changes)
    except Exception as exc:
        raise _handle_store_error(exc) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="work item not found")
    return item


@router.post("/work-items/{work_id}/conversations", status_code=status.HTTP_201_CREATED)
def add_work_conversation(
    work_id: str, payload: WorkConversationCreate, request: Request
) -> Relationship:
    store = _store(request)
    if store.get_work(work_id) is None:
        raise HTTPException(status_code=404, detail="work item not found")
    try:
        return store.attach_work_conversation(
            work_id, payload.conversation_id, relationship_id=_id("rel")
        )
    except Exception as exc:
        raise _handle_store_error(exc) from exc


@router.delete("/work-items/{work_id}/conversations/{conversation_id}", status_code=204)
def remove_work_conversation(work_id: str, conversation_id: str, request: Request) -> Response:
    if not _store(request).detach_work_conversation(work_id, conversation_id):
        raise HTTPException(status_code=404, detail="work conversation association not found")
    return Response(status_code=204)


@router.get("/relationships")
def list_relationships(
    request: Request,
    work_item_id: str | None = None,
    source_kind: EndpointKind | None = None,
    source_id: str | None = None,
    target_kind: EndpointKind | None = None,
    target_id: str | None = None,
    relationship_type: str | None = Query(default=None, alias="type"),
) -> dict[str, Any]:
    items = _store(request).list_relationships(
        work_item_id=work_item_id,
        source_kind=source_kind,
        source_id=source_id,
        target_kind=target_kind,
        target_id=target_id,
        relationship_type=relationship_type,
    )
    return {"items": items, "total": len(items)}


@router.post("/relationships", status_code=status.HTTP_201_CREATED)
def create_relationship(payload: RelationshipCreate, request: Request) -> Relationship:
    relationship = Relationship(
        **payload.model_dump(exclude={"relationship_id"}),
        relationship_id=payload.relationship_id or _id("rel"),
    )
    try:
        return _store(request).create_relationship(relationship)
    except Exception as exc:
        raise _handle_store_error(exc) from exc


@router.delete("/relationships/{relationship_id}", status_code=204)
def delete_relationship(relationship_id: str, request: Request) -> Response:
    if not _store(request).delete_relationship(relationship_id):
        raise HTTPException(status_code=404, detail="relationship not found")
    return Response(status_code=204)


@router.get("/roles")
def list_roles(request: Request, work_item_id: str | None = None) -> dict[str, Any]:
    items = _store(request).list_roles(work_item_id=work_item_id)
    return {"items": items, "total": len(items)}


@router.post("/roles", status_code=status.HTTP_201_CREATED)
def create_role(payload: CoordinatorRoleCreate, request: Request) -> CoordinatorRole:
    role = CoordinatorRole(
        **payload.model_dump(exclude={"role_id"}),
        role_id=payload.role_id or _id("role"),
    )
    try:
        return _store(request).create_role(role)
    except Exception as exc:
        raise _handle_store_error(exc) from exc


@router.get("/roles/{role_id}")
def get_role(role_id: str, request: Request) -> CoordinatorRole:
    role = _store(request).get_role(role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="role not found")
    return role


@router.patch("/roles/{role_id}")
def update_role(role_id: str, payload: CoordinatorRoleUpdate, request: Request) -> CoordinatorRole:
    try:
        role = _store(request).update_role(role_id, payload.changes())
    except Exception as exc:
        raise _handle_store_error(exc) from exc
    if role is None:
        raise HTTPException(status_code=404, detail="role not found")
    return role


@router.get("/roles/{role_id}/checkpoints")
def list_role_checkpoints(role_id: str, request: Request) -> dict[str, Any]:
    items = _store(request).list_checkpoints(role_id)
    return {"items": items, "total": len(items)}


@router.post("/roles/{role_id}/checkpoints", status_code=status.HTTP_201_CREATED)
def create_role_checkpoint(
    role_id: str, payload: RoleCheckpointCreate, request: Request
) -> RoleCheckpoint:
    role = _store(request).get_role(role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="role not found")
    checkpoint = RoleCheckpoint(
        **payload.model_dump(), role_id=role_id, version=role.checkpoint_version + 1
    )
    try:
        return _store(request).append_checkpoint(checkpoint)
    except Exception as exc:
        raise _handle_store_error(exc) from exc


@router.get("/roles/{role_id}/reports")
def list_role_reports(
    role_id: str, request: Request, recipient_role_id: str | None = None
) -> dict[str, Any]:
    items = _store(request).list_reports(role_id, recipient_role_id=recipient_role_id)
    return {"items": items, "total": len(items)}


@router.post("/roles/{role_id}/reports", status_code=status.HTTP_201_CREATED)
def create_role_report(role_id: str, payload: RoleReportCreate, request: Request) -> RoleReport:
    report = RoleReport(
        **payload.model_dump(exclude={"report_id"}),
        report_id=payload.report_id or _id("report"),
        reporting_role_id=role_id,
    )
    try:
        return _store(request).append_report(report)
    except Exception as exc:
        raise _handle_store_error(exc) from exc


@router.get("/roles/{role_id}/events")
def list_role_events(role_id: str, request: Request) -> dict[str, Any]:
    items = _store(request).list_events(role_id)
    return {"items": items, "total": len(items)}


@router.get("/roles/{role_id}/conversations")
def list_role_conversations(role_id: str, request: Request) -> dict[str, Any]:
    items = _store(request).list_role_conversations(role_id)
    return {"items": items, "total": len(items)}


@router.post("/roles/{role_id}/conversations", status_code=status.HTTP_201_CREATED)
def attach_role_conversation(
    role_id: str, payload: RoleConversationHandoff, request: Request
) -> Any:
    try:
        return _store(request).attach_conversation(
            role_id, payload.conversation_id, payload.handoff_summary
        )
    except Exception as exc:
        raise _handle_store_error(exc) from exc


@router.post("/roles/{role_id}/conversations/rotate")
def rotate_role_conversation(
    role_id: str, payload: RoleConversationHandoff, request: Request
) -> Any:
    try:
        return _store(request).rotate_conversation(
            role_id, payload.conversation_id, payload.handoff_summary
        )
    except Exception as exc:
        raise _handle_store_error(exc) from exc


@router.get("/roles/{role_id}/handoff")
def generate_role_handoff(role_id: str, request: Request) -> Any:
    try:
        return _store(request).generate_handoff(role_id)
    except Exception as exc:
        raise _handle_store_error(exc) from exc


@router.post("/roles/{role_id}/lease", status_code=status.HTTP_201_CREATED)
def acquire_role_lease(role_id: str, payload: RoleLeaseRequest, request: Request) -> Any:
    try:
        return _store(request).acquire_role_lease(role_id, payload.holder_id, payload.ttl_seconds)
    except Exception as exc:
        raise _handle_store_error(exc) from exc


@router.post("/roles/{role_id}/lease/renew")
def renew_role_lease(role_id: str, payload: RoleLeaseRenewRequest, request: Request) -> Any:
    try:
        return _store(request).renew_role_lease(
            role_id, payload.holder_id, payload.fencing_token, payload.ttl_seconds
        )
    except Exception as exc:
        raise _handle_store_error(exc) from exc


@router.post("/roles/{role_id}/lease/release")
def release_role_lease(role_id: str, payload: RoleLeaseReleaseRequest, request: Request) -> Any:
    try:
        return _store(request).release_role_lease(role_id, payload.holder_id, payload.fencing_token)
    except Exception as exc:
        raise _handle_store_error(exc) from exc


def mount_role_api(app: FastAPI) -> None:
    """Mount the role API on an application with ``app.state.role_store`` configured."""

    app.include_router(router)
