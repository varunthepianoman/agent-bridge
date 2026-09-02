from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace
from typing import Any

import httpx

from agent_bridge_bridge.cli import run
from agent_bridge_mcp import server


def test_cli_send_can_wait_for_acknowledgement() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/messages":
            return httpx.Response(201, json={"message_id": "message-1"})
        return httpx.Response(
            200,
            json={"status": "reached", "waited_for": "acknowledged"},
        )

    status = run(
        [
            "--api-url",
            "https://bridge.test/api/v1",
            "message",
            "--chat",
            "target",
            "--from-chat",
            "source",
            "--wait-for",
            "acknowledged",
            "--timeout",
            "17",
            "inspect this",
        ],
        transport=httpx.MockTransport(handle),
    )

    assert status == 0
    assert [request.url.path for request in requests] == [
        "/api/v1/messages",
        "/api/v1/messages/message-1/wait-receipt",
    ]
    sent = json.loads(requests[0].content)
    waited = json.loads(requests[1].content)
    assert sent["acknowledgement_requested"] is True
    assert waited == {
        "source_conversation_id": "source",
        "until": "acknowledged",
        "timeout_seconds": 17.0,
        "after_revision": None,
    }


def test_cli_rejects_invalid_wait_before_sending() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"message_id": "unexpected"})

    stderr = StringIO()
    status = run(
        [
            "message",
            "--room",
            "room-1",
            "--from-chat",
            "source",
            "--wait-for",
            "claimed",
            "room message",
        ],
        transport=httpx.MockTransport(handle),
        stderr=stderr,
    )

    assert status == 1
    assert "direct --chat" in stderr.getvalue()
    assert requests == []


async def test_mcp_send_waits_with_dynamic_timeout(monkeypatch: Any) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(
        _ctx: Any, _tool: str, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        if path == "/messages":
            return {"message_id": "message-2"}
        return {"status": "timeout"}

    monkeypatch.setattr(server, "_request", request)
    monkeypatch.setattr(
        server, "_runtime", lambda _ctx: SimpleNamespace(wait_slice_seconds=None)
    )

    result = await server.send_message(
        "inspect this",
        target_conversation_id="target",
        source_conversation_id="source",
        wait_for="terminal",
        timeout_seconds=3600,
        ctx=object(),  # type: ignore[arg-type]
    )

    assert result == {"status": "timeout"}
    assert calls[0][2]["json"]["acknowledgement_requested"] is True
    assert calls[1][1] == "/messages/message-2/wait-receipt"
    assert calls[1][2]["timeout"] == 3610


async def test_mcp_rejects_invalid_wait_before_sending(monkeypatch: Any) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(
        _ctx: Any, _tool: str, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {"message_id": "unexpected"}

    monkeypatch.setattr(server, "_request", request)

    try:
        await server.send_message(
            "room message",
            room_id="room-1",
            source_conversation_id="source",
            wait_for="claimed",
            ctx=object(),  # type: ignore[arg-type]
        )
    except ValueError as exc:
        assert "direct conversation" in str(exc)
    else:
        raise AssertionError("room receipt wait should fail")

    assert calls == []


async def test_mcp_receipt_tools_use_receipt_endpoints(monkeypatch: Any) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(
        _ctx: Any, _tool: str, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {"ok": True}

    monkeypatch.setattr(server, "_request", request)
    monkeypatch.setattr(
        server, "_runtime", lambda _ctx: SimpleNamespace(wait_slice_seconds=None)
    )

    assert await server.acknowledge_message(
        "target", "message-3", "working", ctx=object()  # type: ignore[arg-type]
    ) == {"ok": True}
    assert await server.wait_for_receipt(
        "message-3",
        "source",
        until="terminal",
        timeout_seconds=60,
        after_revision=2,
        ctx=object(),  # type: ignore[arg-type]
    ) == {"ok": True}
    assert await server.get_message_status(
        "message-3", ctx=object()  # type: ignore[arg-type]
    ) == {"ok": True}

    assert [call[1] for call in calls] == [
        "/messages/message-3/acknowledge",
        "/messages/message-3/wait-receipt",
        "/messages/message-3",
    ]
    assert calls[1][2]["timeout"] == 70


def test_cli_wait_attention_maps_filters_and_dynamic_timeout() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "timeout", "items": []})

    status = run(
        [
            "--api-url",
            "https://bridge.test/api/v1",
            "wait-attention",
            "--after-cursor",
            "cursor-1",
            "--max-wait-seconds",
            "17",
            "--batch-limit",
            "4",
            "--conversation",
            "conversation-1",
            "--conversation",
            "conversation-2",
            "--category",
            "needs_attention",
            "--kind",
            "provider_failed",
            "--unread-only",
        ],
        transport=httpx.MockTransport(handle),
    )

    assert status == 0
    assert requests[0].url.path == "/api/v1/attention/wait"
    assert json.loads(requests[0].content) == {
        "after_cursor": "cursor-1",
        "max_wait_seconds": 17.0,
        "batch_limit": 4,
        "conversation_ids": ["conversation-1", "conversation-2"],
        "category": "needs_attention",
        "kinds": ["provider_failed"],
        "unread_only": True,
    }


async def test_mcp_wait_for_attention_maps_arguments_and_dynamic_timeout(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(
        _ctx: Any, _tool: str, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {"status": "timeout", "items": []}

    monkeypatch.setattr(server, "_request", request)
    monkeypatch.setattr(
        server, "_runtime", lambda _ctx: SimpleNamespace(wait_slice_seconds=None)
    )

    result = await server.wait_for_attention(
        "cursor-2",
        max_wait_seconds=60,
        batch_limit=3,
        conversation_ids=["conversation-1"],
        category="status",
        kinds=["turn_completed"],
        unread_only=True,
        ctx=object(),  # type: ignore[arg-type]
    )

    assert result["status"] == "timeout"
    assert calls == [
        (
            "POST",
            "/attention/wait",
            {
                "timeout": 70,
                "long_wait": True,
                "timeout_is_continuation": False,
                "json": {
                    "after_cursor": "cursor-2",
                    "max_wait_seconds": 60,
                    "batch_limit": 3,
                    "conversation_ids": ["conversation-1"],
                    "category": "status",
                    "kinds": ["turn_completed"],
                    "unread_only": True,
                },
            },
        )
    ]
