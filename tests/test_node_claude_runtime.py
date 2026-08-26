from __future__ import annotations

import asyncio
from pathlib import Path

from agent_bridge_node.claude_runtime import RemoteClaudeRuntime
from agent_bridge_node.runner import NodeCommand, NodeTurnEvent


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stdout = b""
        self.stderr = b""
        self.completed = asyncio.Event()
        self.terminated = False
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        await self.completed.wait()
        return self.stdout, self.stderr

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.completed.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.completed.set()

    async def wait(self) -> int:
        await self.completed.wait()
        assert self.returncode is not None
        return self.returncode

    def finish(self, returncode: int, *, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.completed.set()


def command(tmp_path: Path) -> NodeCommand:
    return NodeCommand(
        command_id="cmd-claude-start",
        claim_token="claim-claude-start",
        kind="start_conversation",
        environment_id="host",
        provider="claude",
        workspace=str(tmp_path),
        prompt="Do the work",
        model="opus",
        effort="high",
    )


async def test_start_returns_session_before_completion_then_emits_event(tmp_path: Path) -> None:
    process = FakeProcess()
    launches: list[tuple[list[str], Path]] = []

    async def start(argv: list[str], workspace: Path) -> FakeProcess:
        launches.append((argv, workspace))
        return process

    events: list[NodeTurnEvent] = []
    runtime = RemoteClaudeRuntime(
        "claude-custom", node_id="node-lenovo", event_sink=events.append, process_starter=start
    )
    result = await runtime.start(command(tmp_path))

    assert result.status == "succeeded"
    assert result.output["initial_turn_status"] == "inProgress"
    assert result.output["provider_thread_id"]
    assert result.output["provider_turn_id"]
    assert events == []
    argv, workspace = launches[0]
    assert argv[0:2] == ["claude-custom", "--session-id"]
    assert argv[3:] == [
        "--model",
        "opus",
        "--effort",
        "high",
        "--print",
        "Do the work",
    ]
    assert workspace == tmp_path

    process.finish(0, stdout=b"done")
    await runtime.wait_for_background()
    assert len(events) == 1
    assert events[0].provider == "claude"
    assert events[0].status == "completed"
    assert events[0].provider_thread_id == result.output["provider_thread_id"]


async def test_failed_initial_turn_emits_safe_failure_detail(tmp_path: Path) -> None:
    process = FakeProcess()

    async def start(_argv: list[str], _workspace: Path) -> FakeProcess:
        return process

    events: list[NodeTurnEvent] = []
    runtime = RemoteClaudeRuntime(
        "claude", node_id="node", event_sink=events.append, process_starter=start
    )
    result = await runtime.start(command(tmp_path))
    process.finish(1, stderr=b"provider rejected the turn")
    await runtime.wait_for_background()

    assert result.status == "succeeded"
    assert events[0].status == "failed"
    assert events[0].detail == "provider rejected the turn"


async def test_spawn_failure_does_not_create_conversation_or_event(tmp_path: Path) -> None:
    async def start(_argv: list[str], _workspace: Path) -> FakeProcess:
        raise OSError("binary unavailable")

    events: list[NodeTurnEvent] = []
    runtime = RemoteClaudeRuntime(
        "claude", node_id="node", event_sink=events.append, process_starter=start
    )
    result = await runtime.start(command(tmp_path))

    assert result.status == "failed"
    assert result.output.get("provider_thread_id") is None
    assert "binary unavailable" in (result.detail or "")
    assert events == []


async def test_close_terminates_only_active_claude_process(tmp_path: Path) -> None:
    process = FakeProcess()

    async def start(_argv: list[str], _workspace: Path) -> FakeProcess:
        return process

    runtime = RemoteClaudeRuntime(
        "claude", node_id="node", event_sink=lambda _event: None, process_starter=start
    )
    await runtime.start(command(tmp_path))
    await runtime.close()

    assert process.terminated is True
    assert process.killed is False
