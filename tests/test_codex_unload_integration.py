"""Opt-in real Codex handoff test against a local mock model (no credentials).

AGENT_BRIDGE_TEST_CODEX_BIN=/path/to/codex PYTHONPATH=src pytest -q \
    tests/test_codex_unload_integration.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from agent_bridge_providers.codex import AppServerClient, AppServerError


async def test_idle_unload_releases_writer_without_stopping_other_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = os.environ.get("AGENT_BRIDGE_TEST_CODEX_BIN")
    if not binary:
        pytest.skip("set AGENT_BRIDGE_TEST_CODEX_BIN to Codex 0.154.0+")
    model_requested = asyncio.Event()
    finish_turn = asyncio.Event()

    async def model(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            await reader.readexactly(length)
            model_requested.set()
            await finish_turn.wait()
            event = {
                "type": "response.completed",
                "response": {
                    "id": "response-test",
                    "status": "completed",
                    "output": [],
                    "usage": {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
                },
            }
            body = ("event: response.completed\ndata: " + json.dumps(event) + "\n\n").encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(model, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'model = "mock-model"\nmodel_provider = "mock"\n'
        '[model_providers.mock]\nname = "Offline test"\n'
        f'base_url = "http://127.0.0.1:{port}/v1"\nwire_api = "responses"\n'
    )
    owner = AppServerClient.for_codex(binary)
    reader_client = AppServerClient.for_codex(binary)
    try:
        async with asyncio.timeout(30):
            thread = await owner.start_thread(cwd=str(tmp_path))
            thread_id = thread["id"]
            sibling = await owner.start_thread(cwd=str(tmp_path))
            await owner.start_turn(thread_id, "Offline handoff test")
            await model_requested.wait()
            # Unsubscribing must not stop an active turn even with a zero delay.
            await owner.unsubscribe_thread(thread_id)
            assert thread_id in (await owner.request("thread/loaded/list"))["data"]
            view = await reader_client.read_thread(thread_id, include_turns=True)
            assert view["id"] == thread_id
            with pytest.raises(AppServerError, match="active writer"):
                await reader_client.resume_thread(thread_id, cwd=str(tmp_path))
            finish_turn.set()
            while True:
                method, params = await owner.next_notification(timeout=10)
                if method == "thread/closed" and params["threadId"] == thread_id:
                    break
            loaded = (await owner.request("thread/loaded/list"))["data"]
            assert thread_id not in loaded
            assert sibling["id"] in loaded
            resumed = await reader_client.resume_thread(thread_id, cwd=str(tmp_path))
            assert resumed["id"] == thread_id
    finally:
        finish_turn.set()
        await owner.close()
        await reader_client.close()
        server.close()
        await server.wait_closed()
