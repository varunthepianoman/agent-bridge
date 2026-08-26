from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request

from .nodes import NodeAuthenticationError, NodeStore
from .repository import stable_conversation_id
from .schemas import (
    NodeCatalogSyncRequest,
    NodeCommandClaimRequest,
    NodeCommandResultRequest,
    NodeHeartbeatRequest,
    NodeProvisionRequest,
    NodeRegistration,
    NodeTurnEventRequest,
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
    command = store.claim_command(
        payload.node_id,
        provider_capacity_available=payload.provider_capacity_available,
        active_provider_conversations=payload.active_provider_conversations,
    )
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
        result = store.complete_command(
            node_id=payload.node_id,
            command_id=command_id,
            claim_token=payload.claim_token,
            status=payload.status,
            result={"detail": payload.detail, "output": payload.output},
        )
        already_completed = bool(result.pop("_already_completed", False))
        if already_completed:
            return result
        message_id = payload.output.get("message_id")
        if isinstance(message_id, str):
            request.app.state.messages.set_state(
                message_id,
                "delivered" if payload.status == "succeeded" else "failed",
                error=payload.detail if payload.status != "succeeded" else None,
            )
            if payload.status == "succeeded":
                request.app.state.attention.create(
                    category="update",
                    kind="turn_completed",
                    title="Remote conversation finished a Bridge-delivered turn",
                    conversation_id=result.get("conversation_id"),
                    correlation_id=payload.output.get("correlation_id"),
                )
        if payload.status != "succeeded":
            request.app.state.attention.create(
                category="needs_attention",
                kind="node_command_failed",
                title=f"Remote action failed on {payload.node_id}",
                detail=payload.detail or "Remote node reported a failure",
                conversation_id=result.get("conversation_id"),
            )
        elif result.get("kind") == "start_conversation":
            thread_id = payload.output.get("provider_thread_id")
            if isinstance(thread_id, str) and thread_id:
                row = request.app.state.repository.upsert_discovered(
                    {
                        "provider": result.get("provider", "codex"),
                        "provider_thread_id": thread_id,
                        "title": result.get("alias") or "New Bridge conversation",
                        "preview": result.get("prompt", ""),
                        "cwd": result.get("workspace"),
                        "status": "idle",
                        "source_kind": "agent_bridge",
                        "raw_metadata": {
                            "launch_model": result.get("model"),
                            "launch_effort": result.get("effort"),
                        },
                    },
                    node_id=payload.node_id,
                    environment_id=result.get("environment_id", "host"),
                )
                row = request.app.state.repository.select([row.conversation_id])[0]
                request.app.state.attention.create(
                    category="update",
                    kind="agent_started",
                    title=f"Chat {row.conversation_number} was started remotely",
                    conversation_id=row.conversation_id,
                )
        elif result.get("kind") == "deliver_turn" and not isinstance(message_id, str):
            request.app.state.attention.create(
                category="update",
                kind="turn_completed",
                title="Remote conversation finished a turn",
                conversation_id=result.get("conversation_id"),
            )
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NodeAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/node/turn-events")
def turn_event(
    payload: NodeTurnEventRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    store = _store(request)
    _authenticate(store, payload.node_id, authorization)
    conversation_id = stable_conversation_id(
        payload.provider,
        payload.provider_thread_id,
        payload.node_id,
        payload.environment_id,
    )
    try:
        if not request.app.state.repository.get(conversation_id):
            raise ValueError("turn event conversation is not cataloged yet")
        result = store.record_turn_event(payload.model_dump(mode="json"))
        if result["already_recorded"]:
            return result
        if payload.status == "completed":
            request.app.state.attention.create(
                category="update",
                kind="turn_completed",
                title="Remote conversation finished its initial turn",
                conversation_id=conversation_id,
            )
        else:
            request.app.state.attention.create(
                category="needs_attention",
                kind=f"turn_{payload.status}",
                title=f"Remote conversation initial turn {payload.status}",
                detail=payload.detail or "",
                conversation_id=conversation_id,
            )
        return result
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
