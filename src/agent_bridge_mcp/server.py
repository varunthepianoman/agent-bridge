"""Local stdio MCP facade over the Agent Bridge HTTP API."""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Agent Bridge")


def _request(method: str, path: str, **kwargs: Any) -> Any:
    root = os.environ.get("AGENT_BRIDGE_API_URL", "http://127.0.0.1:58081/api/v1")
    with httpx.Client(base_url=root.rstrip("/"), timeout=30) as client:
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
def send_message(
    body: str,
    target_conversation_id: str | None = None,
    room_id: str | None = None,
    source_conversation_id: str | None = None,
    operation: str = "message",
    correlation_id: str | None = None,
) -> Any:
    """Send a durable Bridge message to exactly one selected chat or room."""
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
def start_agent(
    provider: str,
    cwd: str,
    initial_prompt: str,
    alias: str | None = None,
    node_id: str | None = None,
    environment_id: str | None = None,
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
        },
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
