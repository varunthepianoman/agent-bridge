from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator
from io import StringIO
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from agent_bridge_bridge.cli import run
from agent_bridge_catalog.app import create_app
from agent_bridge_catalog.config import Settings


class EmptyProvider:
    async def discover(self, *, include_turns: bool = True) -> AsyncIterator[Any]:
        del include_turns
        if False:
            yield None

    async def close(self) -> None:
        pass


class RecordingRuntime:
    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []
        self.turns: list[dict[str, Any]] = []
        self.turn_completed = threading.Event()

    async def start(self, **kwargs: Any) -> str:
        self.starts.append(kwargs)
        return "thread-started-with-settings"

    async def turn(self, **kwargs: Any) -> None:
        self.turns.append(kwargs)
        self.turn_completed.set()


def settings(tmp_path: Path) -> Settings:
    return Settings(
        state_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'model-effort.db'}",
        node_id="hub",
        environment_id="host",
        discovery_interval_seconds=3600,
    )


def test_http_launch_and_explicit_turn_effort_are_separate(tmp_path: Path) -> None:
    app = create_app(settings=settings(tmp_path), provider=EmptyProvider())
    runtime = RecordingRuntime()
    app.state.runtime = runtime

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/conversations",
            json={
                "provider": "codex",
                "cwd": str(tmp_path),
                "initial_prompt": "Review the implementation",
                "alias": "reviewer",
                "model": "gpt-5.6-sol",
                "effort": "high",
            },
        )
        assert created.status_code == 201
        conversation_id = created.json()["conversation_id"]
        assert runtime.starts == [
            {
                "provider": "codex",
                "cwd": str(tmp_path),
                "prompt": "Review the implementation",
                "model": "gpt-5.6-sol",
                "effort": "high",
            }
        ]
        detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
        assert detail["raw_metadata"] == {
            "launch_model": "gpt-5.6-sol",
            "launch_effort": "high",
        }

        changed = client.post(
            f"/api/v1/conversations/{conversation_id}/turns",
            json={"prompt": "Check the edge cases", "effort": "xhigh"},
        )
        assert changed.status_code == 202
        assert runtime.turn_completed.wait(timeout=1)
        assert runtime.turns == [
            {
                "provider": "codex",
                "provider_thread_id": "thread-started-with-settings",
                "cwd": str(tmp_path),
                "prompt": "Check the edge cases",
                "effort": "xhigh",
            }
        ]

        model_change = client.post(
            f"/api/v1/conversations/{conversation_id}/turns",
            json={"prompt": "Switch models", "model": "gpt-5.6-terra"},
        )
        assert model_change.status_code == 422


def test_cli_passes_launch_settings_and_only_effort_on_later_turns() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201 if request.url.path == "/api/v1/conversations" else 202, json={})

    transport = httpx.MockTransport(handler)
    output = StringIO()
    assert (
        run(
            [
                "start",
                "--provider",
                "codex",
                "--cwd",
                "/work/project",
                "--model",
                "gpt-5.6-sol",
                "--effort",
                "high",
                "Review it",
            ],
            transport=transport,
            stdout=output,
        )
        == 0
    )
    assert (
        run(
            ["turn", "conversation-1", "Go deeper", "--effort", "xhigh"],
            transport=transport,
            stdout=output,
        )
        == 0
    )

    assert json.loads(requests[0].content) == {
        "provider": "codex",
        "cwd": "/work/project",
        "initial_prompt": "Review it",
        "model": "gpt-5.6-sol",
        "effort": "high",
    }
    assert json.loads(requests[1].content) == {"prompt": "Go deeper", "effort": "xhigh"}


def test_turn_schema_rejects_model_changes() -> None:
    from agent_bridge_catalog.core_api import TurnCreate

    rejected = False
    try:
        TurnCreate.model_validate(
            {"prompt": "Switch models", "model": "gpt-5.6-terra"}
        )
    except ValueError:
        rejected = True
    assert rejected
