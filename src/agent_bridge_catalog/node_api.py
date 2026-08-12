from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request

from .nodes import NodeAuthenticationError, NodeStore
from .schemas import (
    NodeCatalogSyncRequest,
    NodeCommandClaimRequest,
    NodeCommandResultRequest,
    NodeHeartbeatRequest,
    NodeProvisionRequest,
    NodeRegistration,
)

router = APIRouter(prefix="/api/v1")


def _store(request: Request) -> NodeStore:
    return request.app.state.node_store  # type: ignore[no-any-return]


def _authenticate(store: NodeStore, node_id: str, authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing node bearer credential")
    credential = authorization.removeprefix("Bearer ").strip()
    try:
        store.authenticate(node_id, credential)
    except NodeAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/nodes", status_code=201)
def provision_node(payload: NodeProvisionRequest, request: Request) -> dict[str, Any]:
    """Provision a node; the reverse proxy must restrict this single-user admin route."""
    try:
        node, credential = _store(request).provision(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"node": node, "credential": credential}


@router.get("/nodes")
def list_nodes(request: Request) -> dict[str, Any]:
    items = _store(request).list_nodes()
    return {"items": items, "total": len(items)}


@router.get("/nodes/{node_id}")
def get_node(node_id: str, request: Request) -> dict[str, Any]:
    node = _store(request).get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    return node


@router.post("/nodes/{node_id}/credentials/rotate")
def rotate_node_credential(
    node_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Authenticate with the current credential and return its replacement once."""

    store = _store(request)
    _authenticate(store, node_id, authorization)
    try:
        credential = store.rotate_credential(node_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"node_id": node_id, "credential": credential}


@router.post("/node/heartbeat")
def heartbeat(
    payload: NodeHeartbeatRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    store = _store(request)
    _authenticate(store, payload.node_id, authorization)
    registration = NodeRegistration(node_id=payload.node_id)
    return store.heartbeat(
        registration,
        ttl_seconds=payload.ttl_seconds,
        capabilities=payload.capabilities,
        metadata=payload.metadata,
    )


@router.post("/node/sync")
def sync_catalog(
    payload: NodeCatalogSyncRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, int]:
    store = _store(request)
    _authenticate(store, payload.registration.node_id, authorization)
    try:
        return store.sync_catalog(payload.registration, payload.conversations, payload.environments)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/node/commands/claim")
def claim_command(
    payload: NodeCommandClaimRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    store = _store(request)
    _authenticate(store, payload.node_id, authorization)
    command = store.claim_command(payload.node_id)
    return {"command": command}


@router.post("/node/commands/{command_id}/result")
def command_result(
    command_id: str,
    payload: NodeCommandResultRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    store = _store(request)
    _authenticate(store, payload.node_id, authorization)
    try:
        return store.complete_command(
            node_id=payload.node_id,
            command_id=command_id,
            claim_token=payload.claim_token,
            status=payload.status,
            result={"detail": payload.detail, "output": payload.output},
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NodeAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/node/commands/{command_id}")
def get_command(command_id: str, request: Request) -> dict[str, Any]:
    command = _store(request).get_command(command_id)
    if command is None:
        raise HTTPException(status_code=404, detail="command not found")
    return command


def mount_node_api(app: FastAPI) -> None:
    app.include_router(router)
