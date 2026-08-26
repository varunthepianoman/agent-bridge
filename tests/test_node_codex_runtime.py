from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, cast

from agent_bridge_node.codex_runtime import RemoteCodexRuntime
from agent_bridge_node.runner import NodeCommand, NodeTurnEvent
from agent_bridge_providers.codex import AppServerClient, AppServerClosedError, AppServerError

NotificationHandler = Callable[[str, Mapping[str, Any]], Awaitable[None] | None]
CloseHandler = Callable[[AppServerClosedError], Awaitable[None] | None]


class FakeClient:
    def __init__(self) -> None:
        self.notification_handlers: list[NotificationHandler] = []
        self.close_handlers: list[CloseHandler] = []
        self.unsubscribed: list[str] = []
        self.resume_error: AppServerError | None = None
        self.started: list[tuple[str, str, str | None, str | None]] = []
        self.resumed: list[str] = []
        self.reads: list[tuple[str, bool]] = []
        self.is_ready = True
        self.start_error: Exception | None = None

    def add_notification_handler(self, handler: NotificationHandler) -> None:
        self.notification_handlers.append(handler)

    def add_close_handler(self, handler: CloseHandler) -> None:
        self.close_handlers.append(handler)

    async def start_thread(self, *, cwd: str, model: str | None = None) -> Mapping[str, Any]:
        return {"id": "thread-new", "cwd": cwd, "model": model}

    async def resume_thread(
        self, thread_id: str, *, cwd: str | None = None
    ) -> Mapping[str, Any]:
        self.resumed.append(thread_id)
        if self.resume_error is not None:
            raise self.resume_error
        return {"id": thread_id, "cwd": cwd}

    async def read_thread(
        self, thread_id: str, *, include_turns: bool = False
    ) -> Mapping[str, Any]:
        self.reads.append((thread_id, include_turns))
        return {
            "id": thread_id,
            "name": "Live task",
            "cwd": "/workspace",
            "status": {"type": "active"},
            "turns": [
                {
                    "items": [
                        {"type": "userMessage", "text": "Check progress"},
                        {"type": "reasoning", "text": "PRIVATE_REASONING"},
                        {"type": "commandExecution", "output": "SECRET_TOOL_OUTPUT"},
                        {"type": "agentMessage", "text": "Still running."},
                    ]
                }
            ],
        }

    async def start_turn(
        self,
        thread_id: str,
        prompt: str,
        *,
        model: str | None = None,
        effort: str | None = None,
    ) -> Mapping[str, Any]:
        if self.start_error is not None:
            raise self.start_error
        self.started.append((thread_id, prompt, model, effort))
        return {"id": f"turn-{len(self.started)}", "status": "inProgress"}

    async def unsubscribe_thread(self, thread_id: str) -> str:
        self.unsubscribed.append(thread_id)
        return "unsubscribed"

    async def complete(self, turn_id: str, *, status: str = "completed") -> None:
        params: dict[str, Any] = {"turn": {"id": turn_id, "status": status}}
        if status == "failed":
            params["turn"]["error"] = {"message": "provider failed"}
        for handler in self.notification_handlers:
            result = handler("turn/completed", params)
            if result is not None:
                await result

    async def crash(self) -> None:
        self.is_ready = False
        error = AppServerClosedError("app-server crashed")
        for handler in self.close_handlers:
            result = handler(error)
            if result is not None:
                await result


def command(tmp_path: Path, *, kind: str = "start_conversation") -> NodeCommand:
    return NodeCommand(
        command_id=f"cmd-{kind}",
        claim_token=f"claim-{kind}",
        kind=kind,
        environment_id="windows-native",
        provider="codex",
        provider_thread_id="thread-existing" if kind == "deliver_turn" else None,
        workspace=str(tmp_path),
        prompt="Do the work",
        model="gpt-5.6-terra",
        effort="medium",
    )


async def test_start_returns_ids_before_completion_then_emits_event(tmp_path: Path) -> None:
    client = FakeClient()
    events: list[NodeTurnEvent] = []
    runtime = RemoteCodexRuntime(
        cast(AppServerClient, client),
        node_id="windows-nuc",
        event_sink=events.append,
    )
    result = await runtime.start(command(tmp_path))

    assert result.status == "succeeded"
    assert result.output == {
        "provider_thread_id": "thread-new",
        "provider_turn_id": "turn-1",
        "initial_turn_status": "inProgress",
    }
    assert events == []

    await client.complete("turn-1")
    await asyncio.sleep(0)
    assert len(events) == 1
    assert events[0].status == "completed"
    assert client.unsubscribed == ["thread-new"]


async def test_start_turn_failure_releases_new_thread_without_fallback(tmp_path: Path) -> None:
    client = FakeClient()
    client.start_error = AppServerError(-32000, "turn rejected")
    runtime = RemoteCodexRuntime(
        cast(AppServerClient, client), node_id="node", event_sink=lambda _: None
    )

    result = await runtime.start(command(tmp_path))

    assert result.status == "failed"
    assert result.detail == "turn rejected"
    assert client.unsubscribed == ["thread-new"]
    await runtime.close()


async def test_deliver_resumes_waits_and_unsubscribes(tmp_path: Path) -> None:
    client = FakeClient()
    runtime = RemoteCodexRuntime(
        cast(AppServerClient, client), node_id="node", event_sink=lambda _: None
    )
    task = asyncio.create_task(runtime.deliver(command(tmp_path, kind="deliver_turn")))
    await asyncio.sleep(0)
    await client.complete("turn-1")
    result = await task

    assert result.status == "succeeded"
    assert client.started == [("thread-existing", "Do the work", None, "medium")]
    assert client.unsubscribed == ["thread-existing"]


async def test_active_writer_is_blocked_without_exec_fallback(tmp_path: Path) -> None:
    client = FakeClient()
    client.resume_error = AppServerError(-32600, "thread already has an active writer")
    runtime = RemoteCodexRuntime(
        cast(AppServerClient, client), node_id="node", event_sink=lambda _: None
    )
    result = await runtime.deliver(command(tmp_path, kind="deliver_turn"))

    assert result.status == "blocked"
    assert client.started == []
    assert client.unsubscribed == []


async def test_read_uses_only_thread_read_and_filters_non_message_items(tmp_path: Path) -> None:
    client = FakeClient()
    runtime = RemoteCodexRuntime(
        cast(AppServerClient, client), node_id="node", event_sink=lambda _: None
    )
    request = command(tmp_path, kind="deliver_turn").model_copy(
        update={"kind": "read_conversation", "prompt": None, "workspace": None}
    )

    result = await runtime.read(request)

    assert result.status == "succeeded"
    assert client.reads == [("thread-existing", True)]
    assert client.resumed == []
    assert client.started == []
    assert client.unsubscribed == []
    assert result.output["conversation"]["transcript_text"] == (
        "user: Check progress\nassistant: Still running."
    )
    assert "PRIVATE_REASONING" not in str(result.output)
    assert "SECRET_TOOL_OUTPUT" not in str(result.output)


async def test_app_server_crash_fails_waiter_without_replaying_turn(tmp_path: Path) -> None:
    client = FakeClient()
    runtime = RemoteCodexRuntime(
        cast(AppServerClient, client), node_id="node", event_sink=lambda _: None
    )
    task = asyncio.create_task(runtime.deliver(command(tmp_path, kind="deliver_turn")))
    await asyncio.sleep(0)
    await client.crash()
    result = await task

    assert result.status == "failed"
    assert "crashed" in (result.detail or "")
    assert len(client.started) == 1
    assert client.unsubscribed == []
