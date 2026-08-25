from __future__ import annotations

import json
from typing import Any

import httpx

from agent_bridge_node.hub import HubClient, HubTransportError
from agent_bridge_node.runner import CommandResult


def test_hub_client_uses_fenced_single_command_contract() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/claim"):
            return httpx.Response(
                200,
                json={
                    "command": {
                        "command_id": "cmd-1",
                        "claim_token": "claim-token-1",
                        "kind": "open_path",
                        "environment_id": "host",
                        "path": "/tmp",
                        "attempt": 1,
                        "status": "claimed",
                    }
                },
            )
        return httpx.Response(200, json={})

    client = HubClient("https://hub.example", "node-secret", transport=httpx.MockTransport(handle))
    client.synchronize(
        {"node_id": "node-a", "display_name": "Node A", "platform": "linux"},
        [{"provider": "codex", "provider_thread_id": "thread-1", "environment_id": "host"}],
        [{"environment_id": "host", "kind": "linux"}],
    )
    command = client.claim_commands("node-a")[0]
    client.report_result(
        "node-a",
        CommandResult(
            command_id=command.command_id,
            claim_token=command.claim_token,
            status="succeeded",
            detail="opened",
        ),
    )
    client.close()

    assert all(request.headers["authorization"] == "Bearer node-secret" for request in requests)
    sync = _body(requests[0])
    assert sync["registration"]["node_id"] == "node-a"
    assert sync["conversations"][0]["environment_id"] == "host"
    claim = _body(requests[1])
    assert claim == {"node_id": "node-a"}
    result = _body(requests[2])
    assert result == {
        "node_id": "node-a",
        "claim_token": "claim-token-1",
        "status": "succeeded",
        "detail": "opened",
        "output": {},
    }


def test_hub_client_sanitizes_transport_errors() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("https://user:secret@hub.example/request-body")

    client = HubClient("https://hub.example", "node-secret", transport=httpx.MockTransport(handle))
    try:
        client.heartbeat({"node_id": "node-a", "ttl_seconds": 30})
    except HubTransportError as error:
        assert str(error) == "Hub transport failed (ConnectError)"
        assert "secret" not in str(error)
    else:
        raise AssertionError("transport error was not wrapped")


def test_hub_client_retries_transient_http_statuses() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="upstream unavailable")

    client = HubClient("https://hub.example", "node-secret", transport=httpx.MockTransport(handle))
    try:
        client.heartbeat({"node_id": "node-a", "ttl_seconds": 30})
    except HubTransportError as error:
        assert str(error) == "Hub returned transient HTTP status 502"
        assert "upstream unavailable" not in str(error)
    else:
        raise AssertionError("transient HTTP status was not wrapped")


def _body(request: httpx.Request) -> dict[str, Any]:
    value = json.loads(request.content)
    assert isinstance(value, dict)
    return value
