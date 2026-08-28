from __future__ import annotations

import json
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


def test_mcp_send_waits_with_dynamic_timeout(monkeypatch: Any) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        if path == "/messages":
            return {"message_id": "message-2"}
        return {"status": "timeout"}

    monkeypatch.setattr(server, "_request", request)

    result = server.send_message(
        "inspect this",
        target_conversation_id="target",
        source_conversation_id="source",
        wait_for="terminal",
        timeout_seconds=3600,
    )

    assert result == {"status": "timeout"}
    assert calls[0][2]["json"]["acknowledgement_requested"] is True
    assert calls[1][1] == "/messages/message-2/wait-receipt"
    assert calls[1][2]["timeout"] == 3610


def test_mcp_receipt_tools_use_receipt_endpoints(monkeypatch: Any) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {"ok": True}

    monkeypatch.setattr(server, "_request", request)

    assert server.acknowledge_message("target", "message-3", "working") == {"ok": True}
    assert server.wait_for_receipt(
        "message-3",
        "source",
        until="terminal",
        timeout_seconds=60,
        after_revision=2,
    ) == {"ok": True}
    assert server.get_message_status("message-3") == {"ok": True}

    assert [call[1] for call in calls] == [
        "/messages/message-3/acknowledge",
        "/messages/message-3/wait-receipt",
        "/messages/message-3",
    ]
    assert calls[1][2]["timeout"] == 70
