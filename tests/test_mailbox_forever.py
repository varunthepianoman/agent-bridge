from __future__ import annotations

import asyncio
import json
from io import StringIO
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from agent_bridge_bridge.cli import run
from agent_bridge_mcp import server


@pytest.mark.parametrize("empty_waits", [0, 3])
@pytest.mark.parametrize(
    "terminal",
    [
        {"status": "received", "items": [{"message_id": "one"}, {"message_id": "two"}]},
        {"status": "stopped", "items": []},
    ],
)
def test_cli_forever_returns_only_terminal_result(empty_waits: int, terminal: dict) -> None:
    output = StringIO()
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert output.getvalue() == ""
        requests.append(request)
        assert json.loads(request.content) == {"max_wait_seconds": 60, "batch_limit": 2}
        assert request.extensions["timeout"]["read"] == 70
        result = {"status": "timeout", "items": []} if len(requests) <= empty_waits else terminal
        return httpx.Response(200, json=result)

    assert (
        run(
            ["wait", "conversation-1", "--forever", "--batch-limit", "2"],
            transport=httpx.MockTransport(handle),
            stdout=output,
        )
        == 0
    )
    assert len(requests) == empty_waits + 1
    assert json.loads(output.getvalue()) == terminal


@pytest.mark.parametrize("failure", ["disconnect", "timeout", "conflict", "cancel"])
def test_cli_forever_does_not_retry_failures(failure: str) -> None:
    calls = 0
    output, errors = StringIO(), StringIO()

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"status": "timeout", "items": []})
        if failure == "cancel":
            raise KeyboardInterrupt
        if failure == "timeout":
            raise httpx.ReadTimeout("lost response", request=request)
        if failure == "disconnect":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(409, json={"detail": "listener conflict"})

    def invoke() -> int:
        return run(
            ["wait", "conversation-1", "--forever"],
            transport=httpx.MockTransport(handle),
            stdout=output,
            stderr=errors,
        )

    if failure == "cancel":
        with pytest.raises(KeyboardInterrupt):
            invoke()
    else:
        assert invoke() == 1
        assert errors.getvalue()
    assert calls == 2
    assert not output.getvalue()


def test_cli_rejects_forever_with_explicit_duration() -> None:
    with pytest.raises(SystemExit) as error:
        run(["wait", "conversation-1", "--forever", "--max-wait-seconds", "3600"])
    assert error.value.code == 2


@pytest.mark.parametrize("options, seconds", [([], 3600), (["--max-wait-seconds", "0"], 0)])
def test_cli_timed_wait_still_returns_first_timeout(options: list[str], seconds: int) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert json.loads(request.content)["max_wait_seconds"] == seconds
        return httpx.Response(200, json={"status": "timeout", "items": []})

    output = StringIO()
    assert (
        run(
            ["wait", "conversation-1", *options],
            transport=httpx.MockTransport(handle),
            stdout=output,
        )
        == 0
    )
    assert calls == 1
    assert json.loads(output.getvalue())["status"] == "timeout"


@pytest.mark.parametrize("slice_seconds, expected", [(None, 60), (240, 60), (12, 12)])
async def test_mcp_forever_continues_without_deadline(
    monkeypatch: Any, slice_seconds, expected
) -> None:
    calls = []

    async def request(_ctx, _tool, _method, _path, **kwargs):
        calls.append(kwargs)
        return {"status": "timeout", "items": []}

    monkeypatch.setattr(server, "_request", request)
    monkeypatch.setattr(
        server, "_runtime", lambda _: SimpleNamespace(wait_slice_seconds=slice_seconds)
    )
    arguments = {
        "conversation_id": "conversation-1",
        "forever": True,
        "batch_limit": 2,
        "max_wait_seconds": 0,
    }
    for _ in range(4):
        result = await server.wait_mailbox(**arguments, ctx=object())
        assert result["status"] == "continue"
        assert "wait_until" not in result
        assert "remaining_wait_seconds" not in result
        assert result["continuation"] == {
            "tool": "wait_mailbox",
            "arguments": {"conversation_id": "conversation-1", "batch_limit": 2, "forever": True},
        }
        arguments = result["continuation"]["arguments"]
    assert len(calls) == 4
    assert all(call["json"] == {"max_wait_seconds": expected, "batch_limit": 2} for call in calls)


@pytest.mark.parametrize(
    "result",
    [
        {"status": "received", "items": [{"message_id": "one"}]},
        {"status": "stopped", "items": []},
        {"status": "error", "detail": "failed"},
    ],
)
async def test_mcp_forever_returns_terminal_response(monkeypatch: Any, result: dict) -> None:
    async def request(*args, **kwargs):
        return result

    monkeypatch.setattr(server, "_request", request)
    monkeypatch.setattr(server, "_runtime", lambda _: SimpleNamespace(wait_slice_seconds=None))
    assert await server.wait_mailbox("conversation-1", forever=True, ctx=object()) == result


@pytest.mark.parametrize(
    "failure",
    [
        asyncio.CancelledError(),
        httpx.ReadTimeout("lost response"),
        httpx.ConnectError("offline"),
        httpx.HTTPStatusError(
            "conflict", request=httpx.Request("POST", "http://test"), response=httpx.Response(409)
        ),
    ],
)
async def test_mcp_forever_propagates_failure(monkeypatch: Any, failure: BaseException) -> None:
    calls = 0

    async def request(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise failure

    monkeypatch.setattr(server, "_request", request)
    monkeypatch.setattr(server, "_runtime", lambda _: SimpleNamespace(wait_slice_seconds=None))
    with pytest.raises(type(failure)):
        await server.wait_mailbox("conversation-1", forever=True, ctx=object())
    assert calls == 1


async def test_mcp_forever_rejects_deadline() -> None:
    with pytest.raises(ValueError, match="forever cannot be combined with wait_until"):
        await server.wait_mailbox(
            "conversation-1", forever=True, wait_until="2030-01-01T00:00:00Z", ctx=object()
        )
