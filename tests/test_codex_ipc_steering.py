from __future__ import annotations

import asyncio
import json
import os
import struct
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from agent_bridge_providers import ActiveTurnDeliveryState
from agent_bridge_providers.codex.ipc_steering import CodexIpcSteering

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


async def _read(reader: asyncio.StreamReader) -> dict[str, Any]:
    size = struct.unpack("<I", await reader.readexactly(4))[0]
    value = json.loads(await reader.readexactly(size))
    assert isinstance(value, dict)
    return value


async def _write(
    writer: asyncio.StreamWriter,
    value: Mapping[str, Any],
    *,
    split: bool = False,
) -> None:
    payload = json.dumps(value, separators=(",", ":")).encode()
    frame = struct.pack("<I", len(payload)) + payload
    if split:
        for byte in frame:
            writer.write(bytes((byte,)))
            await writer.drain()
    else:
        writer.write(frame)
        await writer.drain()


@asynccontextmanager
async def _server(tmp_path: Path, handler: Handler) -> AsyncIterator[Path]:
    directory = tmp_path / "ipc"
    directory.mkdir(mode=0o700)
    path = directory / "ipc.sock"
    server = await asyncio.start_unix_server(handler, path=path)
    try:
        yield path
    finally:
        server.close()
        await server.wait_closed()


async def _initialize(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    request = await _read(reader)
    assert request["method"] == "initialize"
    await _write(
        writer,
        {
            "type": "response",
            "requestId": request["requestId"],
            "resultType": "success",
            "method": "initialize",
            "result": {"clientId": "bridge-client"},
        },
        split=True,
    )


async def test_steers_through_discovered_owner_and_declines_unrelated_requests(
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _initialize(reader, writer)
        owner = await _read(reader)
        assert owner["method"] == "thread-owner-discovery"
        assert owner["version"] == 1
        await _write(
            writer,
            {
                "type": "client-discovery-request",
                "requestId": "unrelated",
                "request": {"method": "ide-context"},
            },
        )
        declined = await _read(reader)
        assert declined == {
            "type": "client-discovery-response",
            "requestId": "unrelated",
            "response": {"canHandle": False},
        }
        await _write(
            writer,
            {
                "type": "response",
                "requestId": owner["requestId"],
                "resultType": "success",
                "method": owner["method"],
                "handledByClientId": "owner-client",
                "result": {},
            },
        )
        steer = await _read(reader)
        captured.update(steer)
        await _write(
            writer,
            {
                "type": "response",
                "requestId": steer["requestId"],
                "resultType": "success",
                "method": steer["method"],
                "handledByClientId": "owner-client",
                "result": {"turnId": "turn-active"},
            },
        )
        writer.close()

    async with _server(tmp_path, handler) as socket_path:
        result = await CodexIpcSteering(socket_path=socket_path).deliver(
            provider_thread_id="thread-target",
            cwd="/work/project",
            prompt="authenticated prompt",
            message_id="message-stable",
        )

    assert result.state == ActiveTurnDeliveryState.DELIVERED
    assert captured["method"] == "thread-follower-steer-turn"
    assert captured["targetClientId"] == "owner-client"
    assert captured["version"] == 1
    params = captured["params"]
    assert params["clientUserMessageId"] == "message-stable"
    assert params["input"] == [
        {"type": "text", "text": "authenticated prompt", "text_elements": []}
    ]
    assert params["restoreMessage"]["cwd"] == "/work/project"
    assert params["restoreMessage"]["context"]["workspaceRoots"] == ["/work/project"]


async def test_missing_owner_is_definitive_unavailability(tmp_path: Path) -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _initialize(reader, writer)
        owner = await _read(reader)
        await _write(
            writer,
            {
                "type": "response",
                "requestId": owner["requestId"],
                "resultType": "error",
                "error": "no-client-found",
            },
        )
        writer.close()

    async with _server(tmp_path, handler) as socket_path:
        result = await CodexIpcSteering(socket_path=socket_path).deliver(
            provider_thread_id="thread-target",
            cwd="/work",
            prompt="prompt",
            message_id="message-1",
        )

    assert result.state == ActiveTurnDeliveryState.UNAVAILABLE
    assert "no-client-found" in str(result.detail)


async def test_disconnect_after_steer_dispatch_is_uncertain(tmp_path: Path) -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _initialize(reader, writer)
        owner = await _read(reader)
        await _write(
            writer,
            {
                "type": "response",
                "requestId": owner["requestId"],
                "resultType": "success",
                "handledByClientId": "owner-client",
                "result": {},
            },
        )
        steer = await _read(reader)
        assert steer["method"] == "thread-follower-steer-turn"
        writer.close()

    async with _server(tmp_path, handler) as socket_path:
        result = await CodexIpcSteering(socket_path=socket_path).deliver(
            provider_thread_id="thread-target",
            cwd="/work",
            prompt="prompt",
            message_id="message-1",
        )

    assert result.state == ActiveTurnDeliveryState.UNCERTAIN


async def test_rejects_insecure_ipc_directory(tmp_path: Path) -> None:
    async def handler(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    async with _server(tmp_path, handler) as socket_path:
        os.chmod(socket_path.parent, 0o777)
        result = await CodexIpcSteering(socket_path=socket_path).deliver(
            provider_thread_id="thread-target",
            cwd="/work",
            prompt="prompt",
            message_id="message-1",
        )

    assert result.state == ActiveTurnDeliveryState.UNAVAILABLE
    assert "writable by another user" in str(result.detail)


async def test_rejects_oversized_response_frame(tmp_path: Path) -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _read(reader)
        writer.write(struct.pack("<I", 16 * 1024 * 1024 + 1))
        await writer.drain()
        writer.close()

    async with _server(tmp_path, handler) as socket_path:
        result = await CodexIpcSteering(socket_path=socket_path).deliver(
            provider_thread_id="thread-target",
            cwd="/work",
            prompt="prompt",
            message_id="message-1",
        )

    assert result.state == ActiveTurnDeliveryState.UNAVAILABLE
    assert "frame length" in str(result.detail)


async def test_rejects_malformed_json_response(tmp_path: Path) -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _read(reader)
        payload = b"{not-json"
        writer.write(struct.pack("<I", len(payload)) + payload)
        await writer.drain()
        writer.close()

    async with _server(tmp_path, handler) as socket_path:
        result = await CodexIpcSteering(socket_path=socket_path).deliver(
            provider_thread_id="thread-target",
            cwd="/work",
            prompt="prompt",
            message_id="message-1",
        )

    assert result.state == ActiveTurnDeliveryState.UNAVAILABLE
    assert "malformed JSON" in str(result.detail)


async def test_protocol_version_rejection_falls_back(tmp_path: Path) -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _initialize(reader, writer)
        owner = await _read(reader)
        await _write(
            writer,
            {
                "type": "response",
                "requestId": owner["requestId"],
                "resultType": "error",
                "error": "request-version-mismatch",
            },
        )
        writer.close()

    async with _server(tmp_path, handler) as socket_path:
        result = await CodexIpcSteering(socket_path=socket_path).deliver(
            provider_thread_id="thread-target",
            cwd="/work",
            prompt="prompt",
            message_id="message-1",
        )

    assert result.state == ActiveTurnDeliveryState.UNAVAILABLE
    assert "request-version-mismatch" in str(result.detail)
