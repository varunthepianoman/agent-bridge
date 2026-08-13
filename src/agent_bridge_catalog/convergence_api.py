from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request

from .convergence import ConvergenceController

router = APIRouter(prefix="/api/v1/work-items", tags=["convergence"])


def _controller(request: Request) -> ConvergenceController:
    return request.app.state.convergence_controller  # type: ignore[no-any-return]


@router.post("/{work_id}/convergence/approve-publish")
async def approve_publish(work_id: str, request: Request) -> dict[str, Any]:
    try:
        return await _controller(request).approve_publish(work_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{work_id}/convergence/approve-implementation")
async def approve_implementation(work_id: str, request: Request) -> dict[str, Any]:
    try:
        return await _controller(request).approve_implementation(work_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def mount_convergence_api(app: FastAPI) -> None:
    app.include_router(router)
