from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI, Request
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import CancelledNotification, CancelledNotificationParams

from agent_bridge_mcp import server


@asynccontextmanager
async def _running_api(app: FastAPI) -> AsyncIterator[str]:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    api = uvicorn.Server(
        uvicorn.Config(app, log_level="error", lifespan="off", access_log=False)
    )
    task = asyncio.create_task(api.serve(sockets=[listener]))
    while not api.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}/api/v1"
    finally:
        api.should_exit = True
        await task


def _protocol_test_app() -> tuple[FastAPI, SimpleNamespace]:
    app = FastAPI()
    state = SimpleNamespace(active_waits=0, max_active_waits=0, disconnects=0, stop=False)

    async def long_poll(request: Request, seconds: float) -> dict[str, Any]:
        state.active_waits += 1
        state.max_active_waits = max(state.max_active_waits, state.active_waits)
        deadline = asyncio.get_running_loop().time() + seconds
        try:
            while asyncio.get_running_loop().time() < deadline:
                if await request.is_disconnected():
                    state.disconnects += 1
                    raise asyncio.CancelledError
                await asyncio.sleep(0.02)
            return {"status": "timeout", "items": [], "next_cursor": None}
        finally:
            state.active_waits -= 1

    @app.get("/api/v1/conversations")
    async def conversations() -> dict[str, Any]:
        return {"items": [], "total": 0}

    @app.post("/api/v1/attention/wait")
    async def attention_wait(request: Request) -> dict[str, Any]:
        body = await request.json()
        return await long_poll(request, float(body["max_wait_seconds"]))

    @app.post("/api/v1/messages/{_message_id}/wait-receipt")
    async def receipt_wait(_message_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        result = await long_poll(request, float(body["timeout_seconds"]))
        result["message_id"] = _message_id
        return result

    @app.post("/api/v1/mailbox/{_conversation_id}/wait")
    async def mailbox_wait(_conversation_id: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        state.active_waits += 1
        state.max_active_waits = max(state.max_active_waits, state.active_waits)
        deadline = asyncio.get_running_loop().time() + float(body["max_wait_seconds"])
        try:
            while not state.stop and asyncio.get_running_loop().time() < deadline:
                if await request.is_disconnected():
                    state.disconnects += 1
                    raise asyncio.CancelledError
                await asyncio.sleep(0.02)
            return {"status": "stopped" if state.stop else "timeout", "items": []}
        finally:
            state.active_waits -= 1

    @app.post("/api/v1/mailbox/{_conversation_id}/stop-listener")
    async def stop_mailbox(_conversation_id: str) -> dict[str, Any]:
        state.stop = True
        return {"status": "stop_requested"}

    return app, state


@pytest.mark.asyncio
async def test_one_stdio_session_runs_waits_reads_stops_and_cancellation_concurrently() -> None:
    app, state = _protocol_test_app()
    executable = Path(__file__).parents[1] / ".venv" / "bin" / "agent-bridge-mcp"
    async with _running_api(app) as api_url:
        parameters = StdioServerParameters(
            command=str(executable),
            env={**os.environ, "AGENT_BRIDGE_API_URL": api_url},
        )
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()

            attention = asyncio.create_task(
                session.call_tool(
                    "wait_for_attention", {"max_wait_seconds": 2}
                )
            )
            receipt = asyncio.create_task(
                session.call_tool(
                    "wait_for_receipt",
                    {
                        "message_id": "message-1",
                        "source_conversation_id": "source-1",
                        "timeout_seconds": 2,
                    },
                )
            )
            while state.active_waits < 2:
                await asyncio.sleep(0.01)
            assert state.max_active_waits >= 2

            diagnostics = await session.call_tool("get_mcp_diagnostics")
            diagnostic_data = json.loads(diagnostics.content[0].text)  # type: ignore[union-attr]
            assert diagnostic_data["active_request_count"] == 2
            assert diagnostic_data["active_wait_count"] == 2

            started = asyncio.get_running_loop().time()
            listed = await session.call_tool("list_conversations")
            assert not listed.isError
            assert asyncio.get_running_loop().time() - started < 0.5

            await session.send_notification(
                CancelledNotification(
                    params=CancelledNotificationParams(
                        requestId=1, reason="protocol cancellation test"
                    )
                )
            )
            while state.active_waits > 1:
                await asyncio.sleep(0.01)
            with pytest.raises(McpError, match="Request cancelled"):
                await attention
            assert not receipt.done()

            mailbox = asyncio.create_task(
                session.call_tool(
                    "wait_mailbox",
                    {"conversation_id": "conversation-1", "max_wait_seconds": 2},
                )
            )
            while state.active_waits < 2:
                await asyncio.sleep(0.01)
            stopped = await session.call_tool(
                "stop_listener", {"conversation_id": "conversation-1"}
            )
            assert not stopped.isError
            assert not (await mailbox).isError

            await session.send_notification(
                CancelledNotification(
                    params=CancelledNotificationParams(
                        requestId=2, reason="protocol cancellation test"
                    )
                )
            )
            while state.active_waits:
                await asyncio.sleep(0.01)
            with pytest.raises(McpError, match="Request cancelled"):
                await receipt
            assert state.disconnects >= 2


@pytest.mark.asyncio
async def test_sliced_attention_wait_preserves_deadline_and_cursor(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    async def request(
        _ctx: Any, _tool: str, _method: str, _path: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(kwargs)
        return {"status": "timeout", "items": [], "next_cursor": "cursor-2"}

    monkeypatch.setattr(server, "_request", request)
    monkeypatch.setattr(
        server, "_runtime", lambda _ctx: SimpleNamespace(wait_slice_seconds=240)
    )
    result = await server.wait_for_attention(
        after_cursor="cursor-1",
        max_wait_seconds=600,
        ctx=object(),  # type: ignore[arg-type]
    )

    assert result["status"] == "continue"
    assert result["remaining_wait_seconds"] <= 600
    assert result["continuation"] == {
        "tool": "wait_for_attention",
        "arguments": {
            "after_cursor": "cursor-2",
            "batch_limit": 50,
            "conversation_ids": None,
            "category": None,
            "kinds": None,
            "unread_only": False,
            "wait_until": result["wait_until"],
        },
    }
    assert calls[0]["json"]["max_wait_seconds"] == 240

    mailbox = await server.wait_mailbox(
        conversation_id="conversation-1",
        max_wait_seconds=600,
        ctx=object(),  # type: ignore[arg-type]
    )
    assert mailbox["status"] == "continue"
    assert mailbox["continuation"]["arguments"]["conversation_id"] == "conversation-1"
    assert mailbox["continuation"]["arguments"]["wait_until"] == mailbox["wait_until"]


@pytest.mark.asyncio
async def test_sliced_receipt_and_send_continuations_do_not_resend(monkeypatch: Any) -> None:
    paths: list[str] = []

    async def request(
        _ctx: Any, _tool: str, _method: str, path: str, **_kwargs: Any
    ) -> dict[str, Any]:
        paths.append(path)
        if path == "/messages":
            return {"message_id": "message-once"}
        return {
            "status": "timeout",
            "message_id": "message-once",
            "receipt": {"revision": 7},
        }

    monkeypatch.setattr(server, "_request", request)
    monkeypatch.setattr(
        server, "_runtime", lambda _ctx: SimpleNamespace(wait_slice_seconds=240)
    )
    sent = await server.send_message(
        "inspect",
        target_conversation_id="target",
        source_conversation_id="source",
        wait_for="terminal",
        timeout_seconds=600,
        ctx=object(),  # type: ignore[arg-type]
    )
    assert sent["status"] == "continue"
    assert sent["message_id"] == "message-once"
    continuation = sent["continuation"]["arguments"]
    assert continuation["until"] == "terminal"

    receipt = await server.wait_for_receipt(
        **continuation,
        ctx=object(),  # type: ignore[arg-type]
    )
    assert receipt["status"] == "continue"
    assert paths == [
        "/messages",
        "/messages/message-once/wait-receipt",
        "/messages/message-once/wait-receipt",
    ]


@pytest.mark.asyncio
async def test_expired_deadline_is_timeout_and_unsliced_wait_uses_full_duration(
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    async def request(
        _ctx: Any, _tool: str, _method: str, _path: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(kwargs)
        return {"status": "timeout", "items": [], "next_cursor": None}

    monkeypatch.setattr(server, "_request", request)
    runtime = SimpleNamespace(wait_slice_seconds=None)
    monkeypatch.setattr(server, "_runtime", lambda _ctx: runtime)
    unsliced = await server.wait_for_attention(
        max_wait_seconds=3600,
        ctx=object(),  # type: ignore[arg-type]
    )
    assert unsliced["status"] == "timeout"
    assert calls[-1]["json"]["max_wait_seconds"] == 3600

    runtime.wait_slice_seconds = 240
    expired = await server.wait_for_attention(
        max_wait_seconds=3600,
        wait_until=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        ctx=object(),  # type: ignore[arg-type]
    )
    assert expired["status"] == "timeout"
    assert calls[-1]["json"]["max_wait_seconds"] == 0
