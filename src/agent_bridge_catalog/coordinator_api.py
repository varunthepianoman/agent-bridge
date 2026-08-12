from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_bridge_protocol.models import (
    ArtifactRef,
    AuthorityLimits,
    AutonomyMode,
    CoordinatorIntakeStatus,
    EndpointKind,
    EndpointRef,
    WorkRequest,
)

from .coordinator_runtime import CoordinatorRuntime, CoordinatorRuntimeUnavailable
from .coordinator_store import CoordinatorStore

router = APIRouter(prefix="/api/v1/coordinator", tags=["coordinator"])


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntakeCreate(_Input):
    objective: str = Field(min_length=1, max_length=50_000)
    mode: AutonomyMode = AutonomyMode.DELEGATE
    work_id: str | None = None
    target_role_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    authority: AuthorityLimits | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def autonomous_authority_is_explicit_and_bounded(self) -> IntakeCreate:
        if self.mode != AutonomyMode.AUTONOMOUS:
            return self
        if self.authority is None:
            raise ValueError("autonomous mode requires explicit authority")
        explicitly_set = self.authority.model_fields_set
        required = {
            "max_parallel_executions",
            "max_attempts",
            "allowed_capabilities",
            "deadline",
        }
        missing = required - explicitly_set
        if missing:
            raise ValueError(
                f"autonomous authority fields must be explicit: {', '.join(sorted(missing))}"
            )
        if not ({"token_budget", "cost_budget_usd"} & explicitly_set):
            raise ValueError("autonomous mode requires an explicit token or cost budget")
        if not self.authority.allowed_capabilities:
            raise ValueError("autonomous mode requires at least one allowed capability")
        if not (self.work_id or self.target_role_id or self.authority.allowed_work_ids):
            raise ValueError("autonomous mode requires an explicit work or role scope")
        if (
            self.work_id
            and self.authority.allowed_work_ids
            and self.work_id not in self.authority.allowed_work_ids
        ):
            raise ValueError("work_id is outside autonomous authority")
        if self.authority.may_expand_scope:
            raise ValueError("initial autonomous intake cannot authorize scope expansion")
        return self


class IntakeDecision(_Input):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=20_000)
    authority: AuthorityLimits | None = None


class ActivationCreate(_Input):
    intake_request_id: str = Field(min_length=1, max_length=160)


def _store(request: Request) -> CoordinatorStore:
    return request.app.state.coordinator_store  # type: ignore[no-any-return]


def _runtime(request: Request) -> CoordinatorRuntime:
    return request.app.state.coordinator_runtime  # type: ignore[no-any-return]


def _error(exc: Exception) -> HTTPException:
    message = str(exc).strip("'")
    lowered = message.lower()
    if isinstance(exc, KeyError) or "unknown" in lowered or "not found" in lowered:
        return HTTPException(status_code=404, detail=message)
    if any(word in lowered for word in ("conflict", "stale", "lease", "terminal", "authority")):
        return HTTPException(status_code=409, detail=message)
    return HTTPException(status_code=422, detail=message)


@router.get("/runtime")
def runtime_status(request: Request) -> dict[str, Any]:
    return _runtime(request).status()


@router.post("/intake", status_code=status.HTTP_201_CREATED)
async def create_intake(payload: IntakeCreate, request: Request) -> Any:
    if payload.mode == AutonomyMode.MANUAL:
        raise HTTPException(
            status_code=409,
            detail="manual mode bypasses coordinator intake; submit /api/v1/bridge/requests",
        )
    try:
        _runtime(request).require_available()
    except CoordinatorRuntimeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    work_request = WorkRequest(
        request_id=f"intake-{uuid4().hex}",
        objective=payload.objective,
        mode=payload.mode,
        requested_by=EndpointRef(kind=EndpointKind.ENDPOINT, id="catalog-user"),
        work_id=payload.work_id,
        target_role_id=payload.target_role_id,
        context=payload.context,
        authority=payload.authority or AuthorityLimits(),
        artifacts=payload.artifacts,
        extensions=payload.extensions,
    )
    try:
        intake = _store(request).create_intake(work_request)
        _runtime(request).schedule(intake.request_id)
        return intake
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/intake")
def list_intake(
    request: Request,
    status_filter: Annotated[CoordinatorIntakeStatus | None, Query(alias="status")] = None,
    mode: AutonomyMode | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items, total = _store(request).list_intakes(
        status=str(status_filter) if status_filter else None,
        mode=str(mode) if mode else None,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/intake/{request_id}")
def get_intake(request_id: str, request: Request) -> Any:
    item = _store(request).get_intake(request_id)
    if item is None:
        raise HTTPException(status_code=404, detail="intake not found")
    return item


@router.post("/intake/{request_id}/decision")
async def decide_intake(request_id: str, payload: IntakeDecision, request: Request) -> Any:
    try:
        decided = _store(request).decide_intake(
            request_id,
            approved=payload.decision == "approve",
            note=payload.note,
            authority=payload.authority,
        )
        if payload.decision == "approve" and decided.request.mode in {
            AutonomyMode.DELEGATE,
            AutonomyMode.AUTONOMOUS,
        }:
            _runtime(request).require_available()
            _runtime(request).schedule(request_id, role_id=decided.routed_role_id)
        return decided
    except CoordinatorRuntimeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/intake/{request_id}/events")
def list_intake_events(request_id: str, request: Request) -> dict[str, Any]:
    try:
        items = _store(request).list_intake_events(request_id)
    except Exception as exc:
        raise _error(exc) from exc
    return {"items": items, "total": len(items)}


@router.get("/roles/{role_id}/context")
def role_context(role_id: str, request: Request, intake_request_id: str | None = None) -> Any:
    try:
        return _store(request).assemble_context(role_id, intake_request_id=intake_request_id)
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/roles/{role_id}/activations", status_code=status.HTTP_202_ACCEPTED)
async def begin_activation(role_id: str, payload: ActivationCreate, request: Request) -> Any:
    intake = _store(request).get_intake(payload.intake_request_id)
    if intake is None:
        raise HTTPException(status_code=404, detail="intake not found")
    try:
        _runtime(request).require_available()
        _runtime(request).schedule(payload.intake_request_id, role_id=role_id)
    except CoordinatorRuntimeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "accepted": True,
        "role_id": role_id,
        "intake_request_id": payload.intake_request_id,
        "intake": intake,
    }


@router.get("/roles/{role_id}/activations")
def list_role_activations(role_id: str, request: Request) -> dict[str, Any]:
    items = _store(request).list_activations(role_id=role_id)
    return {"items": items, "total": len(items)}


@router.get("/activations/{activation_id}")
def get_activation(activation_id: str, request: Request) -> Any:
    item = _store(request).get_activation(activation_id)
    if item is None:
        raise HTTPException(status_code=404, detail="activation not found")
    return item


@router.get("/roles/{role_id}/rollups")
def list_role_rollups(role_id: str, request: Request) -> dict[str, Any]:
    try:
        items = _store(request).list_rollups(role_id)
    except Exception as exc:
        raise _error(exc) from exc
    return {"items": items, "total": len(items)}


def mount_coordinator_api(app: FastAPI) -> None:
    app.include_router(router)
