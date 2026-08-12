from __future__ import annotations

import io
import json

import httpx

from agent_bridge_bridge.cli import run


def test_cli_submits_request_from_stdin() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"execution": {"execution_id": "exec-1"}})

    stdout = io.StringIO()
    code = run(
        ["--api-url", "http://hub/api/v1", "request-submit", "--json", "-"],
        transport=httpx.MockTransport(handler),
        stdin=io.StringIO('{"request":{"operation":"new_execution"}}'),
        stdout=stdout,
    )
    assert code == 0
    assert seen == {
        "method": "POST",
        "path": "/api/v1/bridge/requests",
        "body": {"request": {"operation": "new_execution"}},
    }
    assert "exec-1" in stdout.getvalue()


def test_cli_cancel_and_http_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/cancel"):
            assert json.loads(request.content) == {"reason": "stop now"}
            return httpx.Response(200, json={"execution": {"status": "cancelled"}})
        return httpx.Response(404, json={"detail": "missing"})

    transport = httpx.MockTransport(handler)
    assert (
        run(
            [
                "--api-url",
                "http://hub/api/v1",
                "execution-cancel",
                "exec-1",
                "--reason",
                "stop now",
            ],
            transport=transport,
            stdout=io.StringIO(),
        )
        == 0
    )
    stderr = io.StringIO()
    assert (
        run(
            ["--api-url", "http://hub/api/v1", "execution-status", "missing"],
            transport=transport,
            stdout=io.StringIO(),
            stderr=stderr,
        )
        == 1
    )
    assert "404" in stderr.getvalue()
