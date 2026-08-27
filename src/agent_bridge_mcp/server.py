"""Local stdio MCP facade over the Agent Bridge HTTP API."""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Agent Bridge")


def _request(
    method: str, path: str, *, timeout: float = 30, **kwargs: Any
) -> Any:
    root = os.environ.get("AGENT_BRIDGE_API_URL", "http://127.0.0.1:58080/api/v1")
    with httpx.Client(base_url=root.rstrip("/"), timeout=timeout) as client:
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {"ok": True}


@mcp.tool()
def list_conversations(query: str | None = None, provider: str | None = None) -> Any:
    """List selected conversations in the private Bridge directory."""
    return _request("GET", "/conversations", params={"q": query, "provider": provider})


@mcp.tool()
def get_conversation(conversation_id: str) -> Any:
    """Read one selected conversation, including its current transcript projection."""
    return _request("GET", f"/conversations/{conversation_id}")


@mcp.tool()
def refresh_conversation(conversation_id: str, wait_seconds: float = 10) -> Any:
    """Safely refresh a remote Codex transcript using read-only thread/read."""
    return _request(
        "POST",
        f"/conversations/{conversation_id}/refresh",
        params={"wait_seconds": wait_seconds},
    )


@mcp.tool()
def send_message(
    body: str,
    target_conversation_id: str | None = None,
    room_id: str | None = None,
    source_conversation_id: str | None = None,
    operation: str = "message",
    correlation_id: str | None = None,
) -> Any:
    """Put durable mail in exactly one selected chat or room inbox."""
    return _request(
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
        },
    )


@mcp.tool()
def list_inbox(
    conversation_id: str, state: str | None = None, limit: int = 200
) -> Any:
    """List durable mail and processing state for exactly one selected conversation."""
    return _request(
        "GET",
        f"/mailbox/{conversation_id}",
        params={"state": state, "limit": limit},
    )


@mcp.tool()
def wait_mailbox(
    conversation_id: str,
    max_wait_seconds: float = 3600,
    batch_limit: int = 50,
) -> Any:
    """Wait in this foreground turn for mail, returning it only as structured tool data."""
    return _request(
        "POST",
        f"/mailbox/{conversation_id}/wait",
        timeout=max(30, max_wait_seconds + 10),
        json={"max_wait_seconds": max_wait_seconds, "batch_limit": batch_limit},
    )


@mcp.tool()
def complete_message(
    conversation_id: str,
    message_id: str,
    outcome: str,
    detail: str | None = None,
    reply_body: str | None = None,
) -> Any:
    """Record a received message outcome, optionally sending one correlated mailbox reply."""
    return _request(
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
def requeue_message(
    conversation_id: str, message_id: str, detail: str | None = None
) -> Any:
    """Explicitly return a received or terminal mailbox delivery to pending."""
    return _request(
        "POST",
        f"/messages/{message_id}/requeue",
        json={"conversation_id": conversation_id, "detail": detail},
    )


@mcp.tool()
def stop_listener(conversation_id: str) -> Any:
    """Request cancellation of the active foreground mailbox listener."""
    return _request("POST", f"/mailbox/{conversation_id}/stop-listener")


@mcp.tool()
def start_agent(
    provider: str,
    cwd: str,
    initial_prompt: str,
    alias: str | None = None,
    node_id: str | None = None,
    environment_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> Any:
    """Start a full Codex or Claude conversation in a trusted Bridge environment."""
    return _request(
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
def send_turn(conversation_id: str, prompt: str, effort: str | None = None) -> Any:
    """Send an explicit user turn, optionally changing reasoning effort for later turns."""
    return _request(
        "POST",
        f"/conversations/{conversation_id}/turns",
        json={"prompt": prompt, "effort": effort},
    )


@mcp.tool()
def steer_active_turn(conversation_id: str, prompt: str) -> Any:
    """Explicitly steer a local active Codex turn; never fall back to a new turn."""
    return _request(
        "POST", f"/conversations/{conversation_id}/steer", json={"prompt": prompt}
    )


@mcp.tool()
def open_conversation(conversation_id: str) -> Any:
    """Open a selected conversation in its native provider UI."""
    return _request("POST", f"/conversations/{conversation_id}/open")


@mcp.tool()
def list_attention(category: str | None = None, unread_only: bool = False) -> Any:
    """List status updates or items that need the user's attention."""
    return _request("GET", "/attention", params={"category": category, "unread_only": unread_only})


@mcp.tool()
def acknowledge_attention(attention_id: str) -> Any:
    """Acknowledge one attention item."""
    return _request("POST", f"/attention/{attention_id}/acknowledge")


@mcp.tool()
def list_nodes() -> Any:
    """List connected machines and execution environments."""
    return _request("GET", "/nodes")


@mcp.tool()
def list_rooms() -> Any:
    """List lightweight Bridge rooms and their delivery modes."""
    return _request("GET", "/rooms")


def main() -> None:
    mcp.run(transport="stdio")
