"""Local stdio MCP facade over the Agent Bridge HTTP API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.server import Settings as FastMCPSettings

logger = logging.getLogger(__name__)


@dataclass
class McpRuntime:
    """Process-local pooled client and in-flight request diagnostics."""

    client: httpx.AsyncClient
    wait_slice_seconds: float | None
    active_requests: dict[str, str] = field(default_factory=dict)
    active_waits: dict[str, str] = field(default_factory=dict)
    outcomes: Counter[str] = field(default_factory=Counter)


def _api_root() -> str:
    return os.environ.get("AGENT_BRIDGE_API_URL", "http://127.0.0.1:58080/api/v1").rstrip(
        "/"
    )


def _configured_wait_slice() -> float | None:
    raw = os.environ.get("AGENT_BRIDGE_MCP_WAIT_SLICE_SECONDS")
    if raw is None or not raw.strip():
        return None
    value = float(raw)
    if value <= 0:
        raise ValueError("AGENT_BRIDGE_MCP_WAIT_SLICE_SECONDS must be positive")
    return value


@asynccontextmanager
async def _lifespan(_server: FastMCP[Any]) -> AsyncIterator[McpRuntime]:
    async with httpx.AsyncClient(base_url=_api_root(), timeout=30) as client:
        yield McpRuntime(client=client, wait_slice_seconds=_configured_wait_slice())


# mcp 1.x leaves this generic forward reference unresolved until explicitly rebuilt.
FastMCPSettings.model_rebuild()
mcp = FastMCP("Agent Bridge", lifespan=_lifespan)


def _runtime(ctx: Context[Any, Any, Any]) -> McpRuntime:
    runtime = ctx.request_context.lifespan_context
    if not isinstance(runtime, McpRuntime):
        raise RuntimeError("Agent Bridge MCP runtime is unavailable")
    return runtime


async def _request(
    ctx: Context[Any, Any, Any],
    tool_name: str,
    method: str,
    path: str,
    *,
    timeout: float = 30,
    long_wait: bool = False,
    timeout_is_continuation: bool = False,
    **kwargs: Any,
) -> Any:
    runtime = _runtime(ctx)
    request_id = str(ctx.request_id)
    started = time.monotonic()
    outcome = "received"
    runtime.active_requests[request_id] = tool_name
    if long_wait:
        runtime.active_waits[request_id] = tool_name
    try:
        response = await runtime.client.request(method, path, timeout=timeout, **kwargs)
        response.raise_for_status()
        result = response.json() if response.content else {"ok": True}
        if isinstance(result, dict):
            status = result.get("status")
            if status == "timeout":
                outcome = "continuation" if timeout_is_continuation else "timeout"
            elif status == "continue":
                outcome = "continuation"
        return result
    except asyncio.CancelledError:
        outcome = "cancellation"
        raise
    except httpx.TransportError:
        outcome = "disconnect"
        raise
    except Exception:
        outcome = "error"
        raise
    finally:
        runtime.active_requests.pop(request_id, None)
        runtime.active_waits.pop(request_id, None)
        runtime.outcomes[outcome] += 1
        logger.info(
            "agent_bridge_mcp_request %s",
            json.dumps(
                {
                    "tool": tool_name,
                    "request_id": request_id,
                    "elapsed_seconds": round(time.monotonic() - started, 6),
                    "outcome": outcome,
                    "active_request_count": len(runtime.active_requests),
                    "active_wait_count": len(runtime.active_waits),
                },
                sort_keys=True,
            ),
        )


@dataclass(frozen=True)
class WaitBudget:
    wait_until: datetime
    remaining_seconds: float
    request_seconds: float
    sliced: bool


def _parse_wait_until(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("wait_until must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("wait_until must include a timezone")
    return parsed.astimezone(UTC)


def _wait_budget(
    requested_seconds: float,
    wait_until: str | None,
    wait_slice_seconds: float | None,
) -> WaitBudget:
    if not 0 <= requested_seconds <= 3600:
        raise ValueError("wait duration must be between 0 and 3600 seconds")
    now = datetime.now(UTC)
    deadline = (
        now + timedelta(seconds=requested_seconds)
        if wait_until is None
        else _parse_wait_until(wait_until)
    )
    remaining = max(0.0, (deadline - now).total_seconds())
    if remaining > 3600.5:
        raise ValueError("wait_until cannot be more than 3600 seconds in the future")
    request_seconds = remaining
    sliced = False
    if wait_slice_seconds is not None:
        request_seconds = min(request_seconds, wait_slice_seconds)
        sliced = request_seconds < remaining
    return WaitBudget(deadline, remaining, request_seconds, sliced)


def _iso_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _continue_wait(
    result: Any,
    budget: WaitBudget,
    *,
    continuation_tool: str,
    continuation_arguments: dict[str, Any],
) -> Any:
    if (
        not budget.sliced
        or not isinstance(result, dict)
        or result.get("status") != "timeout"
    ):
        return result
    remaining = max(0.0, (budget.wait_until - datetime.now(UTC)).total_seconds())
    if remaining <= 0:
        return result
    continued = dict(result)
    continued.update(
        {
            "status": "continue",
            "wait_until": _iso_timestamp(budget.wait_until),
            "remaining_wait_seconds": remaining,
            "continuation": {
                "tool": continuation_tool,
                "arguments": {
                    **continuation_arguments,
                    "wait_until": _iso_timestamp(budget.wait_until),
                },
            },
        }
    )
    return continued


@mcp.tool()
async def list_conversations(
    query: str | None = None,
    provider: str | None = None,
    ctx: Context[Any, Any, Any] | None = None,
) -> Any:
    """List selected conversations in the private Bridge directory."""
    assert ctx is not None
    return await _request(
        ctx,
        "list_conversations",
        "GET",
        "/conversations",
        params={"q": query, "provider": provider},
    )


@mcp.tool()
async def get_conversation(
    conversation_id: str, ctx: Context[Any, Any, Any] | None = None
) -> Any:
    """Read one selected conversation, including its current transcript projection."""
    assert ctx is not None
    return await _request(ctx, "get_conversation", "GET", f"/conversations/{conversation_id}")


@mcp.tool()
async def refresh_conversation(
    conversation_id: str,
    wait_seconds: float = 10,
    last_message_only: bool = False,
    ctx: Context[Any, Any, Any] | None = None,
) -> Any:
    """Safely refresh a remote Codex transcript, optionally returning its latest reply only."""
    assert ctx is not None
    return await _request(
        ctx,
        "refresh_conversation",
        "POST",
        f"/conversations/{conversation_id}/refresh",
        params={
            "wait_seconds": wait_seconds,
            "last_message_only": last_message_only,
        },
    )


@mcp.tool()
async def send_message(
    body: str,
    target_conversation_id: str | None = None,
    room_id: str | None = None,
    source_conversation_id: str | None = None,
    operation: str = "message",
    correlation_id: str | None = None,
    acknowledgement_requested: bool = False,
    wait_for: str | None = None,
    timeout_seconds: float = 30,
    wait_until: str | None = None,
    ctx: Context[Any, Any, Any] | None = None,
) -> Any:
    """Send durable mail once. On status continue, call wait_for_receipt as instructed."""
    assert ctx is not None
    if wait_for not in {None, "claimed", "acknowledged", "terminal"}:
        raise ValueError("wait_for must be claimed, acknowledged, terminal, or null")
    budget: WaitBudget | None = None
    if wait_for is not None:
        if source_conversation_id is None:
            raise ValueError("source_conversation_id is required when waiting for a receipt")
        if target_conversation_id is None or room_id is not None:
            raise ValueError("receipt waits require a direct conversation target")
        budget = _wait_budget(timeout_seconds, wait_until, _runtime(ctx).wait_slice_seconds)
    request_acknowledgement = acknowledgement_requested or wait_for in {
        "acknowledged",
        "terminal",
    }
    sent = await _request(
        ctx,
        "send_message",
        "POST",
        "/messages",
        json={
            "body": body,
            "target_conversation_id": target_conversation_id,
            "room_id": room_id,
            "source_conversation_id": source_conversation_id,
            "actor_kind": "agent",
            "operation": operation,
            "correlation_id": correlation_id,
            "acknowledgement_requested": request_acknowledgement,
        },
    )
    if wait_for is None:
        return sent
    assert budget is not None
    result = await _request(
        ctx,
        "send_message",
        "POST",
        f"/messages/{sent['message_id']}/wait-receipt",
        timeout=max(30, budget.request_seconds + 10),
        long_wait=True,
        timeout_is_continuation=budget.sliced,
        json={
            "source_conversation_id": source_conversation_id,
            "until": wait_for,
            "timeout_seconds": budget.request_seconds,
            "after_revision": None,
        },
    )
    continued = _continue_wait(
        result,
        budget,
        continuation_tool="wait_for_receipt",
        continuation_arguments={
            "message_id": sent["message_id"],
            "source_conversation_id": source_conversation_id,
            "until": wait_for,
            "after_revision": None,
        },
    )
    if isinstance(continued, dict) and continued.get("status") == "continue":
        continued["message_id"] = sent["message_id"]
    return continued


@mcp.tool()
async def get_message_status(
    message_id: str, ctx: Context[Any, Any, Any] | None = None
) -> Any:
    """Read transport and per-recipient processing status for one durable message."""
    assert ctx is not None
    return await _request(ctx, "get_message_status", "GET", f"/messages/{message_id}")


@mcp.tool()
async def wait_for_receipt(
    message_id: str,
    source_conversation_id: str,
    until: str = "acknowledged",
    timeout_seconds: float = 3600,
    after_revision: int | None = None,
    wait_until: str | None = None,
    ctx: Context[Any, Any, Any] | None = None,
) -> Any:
    """Wait for a receipt. On status continue, call this tool again with returned arguments."""
    assert ctx is not None
    budget = _wait_budget(timeout_seconds, wait_until, _runtime(ctx).wait_slice_seconds)
    result = await _request(
        ctx,
        "wait_for_receipt",
        "POST",
        f"/messages/{message_id}/wait-receipt",
        timeout=max(30, budget.request_seconds + 10),
        long_wait=True,
        timeout_is_continuation=budget.sliced,
        json={
            "source_conversation_id": source_conversation_id,
            "until": until,
            "timeout_seconds": budget.request_seconds,
            "after_revision": after_revision,
        },
    )
    return _continue_wait(
        result,
        budget,
        continuation_tool="wait_for_receipt",
        continuation_arguments={
            "message_id": message_id,
            "source_conversation_id": source_conversation_id,
            "until": until,
            "after_revision": after_revision,
        },
    )


@mcp.tool()
async def list_inbox(
    conversation_id: str,
    state: str | None = None,
    limit: int = 200,
    ctx: Context[Any, Any, Any] | None = None,
) -> Any:
    """List durable mail and processing state for exactly one selected conversation."""
    assert ctx is not None
    return await _request(
        ctx,
        "list_inbox",
        "GET",
        f"/mailbox/{conversation_id}",
        params={"state": state, "limit": limit},
    )


@mcp.tool()
async def wait_mailbox(
    conversation_id: str,
    max_wait_seconds: float = 3600,
    batch_limit: int = 50,
    wait_until: str | None = None,
    ctx: Context[Any, Any, Any] | None = None,
) -> Any:
    """Wait for mail. On status continue, call this tool again with returned arguments."""
    assert ctx is not None
    budget = _wait_budget(max_wait_seconds, wait_until, _runtime(ctx).wait_slice_seconds)
    result = await _request(
        ctx,
        "wait_mailbox",
        "POST",
        f"/mailbox/{conversation_id}/wait",
        timeout=max(30, budget.request_seconds + 10),
        long_wait=True,
        timeout_is_continuation=budget.sliced,
        json={"max_wait_seconds": budget.request_seconds, "batch_limit": batch_limit},
    )
    return _continue_wait(
        result,
        budget,
        continuation_tool="wait_mailbox",
        continuation_arguments={
            "conversation_id": conversation_id,
            "batch_limit": batch_limit,
        },
    )


@mcp.tool()
async def complete_message(
    conversation_id: str,
    message_id: str,
    outcome: str,
    detail: str | None = None,
    reply_body: str | None = None,
    ctx: Context[Any, Any, Any] | None = None,
) -> Any:
    """Record a received message outcome, optionally sending one correlated mailbox reply."""
    assert ctx is not None
    return await _request(
        ctx,
        "complete_message",
        "POST",
        f"/messages/{message_id}/complete",
        json={
            "conversation_id": conversation_id,
            "outcome": outcome,
            "detail": detail,
            "reply_body": reply_body,
        },
    )


@mcp.tool()
async def acknowledge_message(
    conversation_id: str,
    message_id: str,
    detail: str | None = None,
    ctx: Context[Any, Any, Any] | None = None,
) -> Any:
    """Acknowledge requested mail before longer processing; completion already implies this."""
    assert ctx is not None
    return await _request(
        ctx,
        "acknowledge_message",
        "POST",
        f"/messages/{message_id}/acknowledge",
        json={"conversation_id": conversation_id, "detail": detail},
    )


@mcp.tool()
async def requeue_message(
    conversation_id: str,
    message_id: str,
    detail: str | None = None,
    ctx: Context[Any, Any, Any] | None = None,
) -> Any:
    """Explicitly return a received or terminal mailbox delivery to pending."""
    assert ctx is not None
    return await _request(
        ctx,
        "requeue_message",
        "POST",
        f"/messages/{message_id}/requeue",
        json={"conversation_id": conversation_id, "detail": detail},
    )


@mcp.tool()
async def stop_listener(
    conversation_id: str, ctx: Context[Any, Any, Any] | None = None
) -> Any:
    """Request cancellation of the active foreground mailbox listener."""
    assert ctx is not None
    return await _request(
        ctx, "stop_listener", "POST", f"/mailbox/{conversation_id}/stop-listener"
    )


@mcp.tool()
async def start_agent(
    provider: str,
    cwd: str,
    initial_prompt: str,
    alias: str | None = None,
    node_id: str | None = None,
    environment_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    ctx: Context[Any, Any, Any] | None = None,
) -> Any:
    """Start a full Codex or Claude conversation in a trusted Bridge environment."""
    assert ctx is not None
    return await _request(
        ctx,
        "start_agent",
        "POST",
        "/conversations",
        json={
            "provider": provider,
            "cwd": cwd,
            "initial_prompt": initial_prompt,
            "alias": alias,
            "node_id": node_id,
            "environment_id": environment_id,
            "model": model,
            "effort": effort,
        },
    )


@mcp.tool()
async def send_turn(
    conversation_id: str,
    prompt: str,
    effort: str | None = None,
    ctx: Context[Any, Any, Any] | None = None,
) -> Any:
    """Send an explicit user turn, optionally changing reasoning effort for later turns."""
    assert ctx is not None
    return await _request(
        ctx,
        "send_turn",
        "POST",
        f"/conversations/{conversation_id}/turns",
        json={"prompt": prompt, "effort": effort},
    )


@mcp.tool()
async def steer_active_turn(
    conversation_id: str,
    prompt: str,
    ctx: Context[Any, Any, Any] | None = None,
) -> Any:
    """Explicitly steer a local active Codex turn; never fall back to a new turn."""
    assert ctx is not None
    return await _request(
        ctx,
        "steer_active_turn",
        "POST",
        f"/conversations/{conversation_id}/steer",
        json={"prompt": prompt},
    )


@mcp.tool()
async def open_conversation(
    conversation_id: str, ctx: Context[Any, Any, Any] | None = None
) -> Any:
    """Open a selected conversation in its native provider UI."""
    assert ctx is not None
    return await _request(
        ctx, "open_conversation", "POST", f"/conversations/{conversation_id}/open"
    )


@mcp.tool()
async def list_attention(
    category: str | None = None,
    unread_only: bool = False,
    ctx: Context[Any, Any, Any] | None = None,
) -> Any:
    """List status updates or items that need the user's attention."""
    assert ctx is not None
    return await _request(
        ctx,
        "list_attention",
        "GET",
        "/attention",
        params={"category": category, "unread_only": unread_only},
    )


@mcp.tool()
async def wait_for_attention(
    after_cursor: str | None = None,
    max_wait_seconds: float = 3600,
    batch_limit: int = 50,
    conversation_ids: list[str] | None = None,
    category: str | None = None,
    kinds: list[str] | None = None,
    unread_only: bool = False,
    wait_until: str | None = None,
    ctx: Context[Any, Any, Any] | None = None,
) -> Any:
    """Wait for attention. On status continue, call again using its cursor and arguments."""
    assert ctx is not None
    budget = _wait_budget(max_wait_seconds, wait_until, _runtime(ctx).wait_slice_seconds)
    result = await _request(
        ctx,
        "wait_for_attention",
        "POST",
        "/attention/wait",
        timeout=max(30, budget.request_seconds + 10),
        long_wait=True,
        timeout_is_continuation=budget.sliced,
        json={
            "after_cursor": after_cursor,
            "max_wait_seconds": budget.request_seconds,
            "batch_limit": batch_limit,
            "conversation_ids": conversation_ids,
            "category": category,
            "kinds": kinds,
            "unread_only": unread_only,
        },
    )
    next_cursor = result.get("next_cursor") if isinstance(result, dict) else after_cursor
    return _continue_wait(
        result,
        budget,
        continuation_tool="wait_for_attention",
        continuation_arguments={
            "after_cursor": next_cursor,
            "batch_limit": batch_limit,
            "conversation_ids": conversation_ids,
            "category": category,
            "kinds": kinds,
            "unread_only": unread_only,
        },
    )


@mcp.tool()
async def acknowledge_attention(
    attention_id: str, ctx: Context[Any, Any, Any] | None = None
) -> Any:
    """Acknowledge one attention item."""
    assert ctx is not None
    return await _request(
        ctx, "acknowledge_attention", "POST", f"/attention/{attention_id}/acknowledge"
    )


@mcp.tool()
async def list_nodes(ctx: Context[Any, Any, Any] | None = None) -> Any:
    """List connected machines and execution environments."""
    assert ctx is not None
    return await _request(ctx, "list_nodes", "GET", "/nodes")


@mcp.tool()
async def list_rooms(ctx: Context[Any, Any, Any] | None = None) -> Any:
    """List lightweight Bridge rooms and their delivery modes."""
    assert ctx is not None
    return await _request(ctx, "list_rooms", "GET", "/rooms")


@mcp.tool()
async def get_mcp_diagnostics(ctx: Context[Any, Any, Any] | None = None) -> Any:
    """Read process-local MCP request and long-wait concurrency diagnostics."""
    assert ctx is not None
    runtime = _runtime(ctx)
    return {
        "active_request_count": len(runtime.active_requests),
        "active_wait_count": len(runtime.active_waits),
        "active_requests": dict(runtime.active_requests),
        "active_waits": dict(runtime.active_waits),
        "outcomes": dict(runtime.outcomes),
        "wait_slice_seconds": runtime.wait_slice_seconds,
    }


def main() -> None:
    mcp.run(transport="stdio")
