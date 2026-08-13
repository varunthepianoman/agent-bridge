from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_bridge_bridge.execution_store import SQLiteExecutionStore
from agent_bridge_bridge.runners import (
    AllowedCommand,
    AllowlistedCommandRunner,
    CancellationToken,
    CodexSDKRunner,
    ExecutionCancelled,
    ExecutionDispatcher,
    OpenAICodexClient,
    RunnerOutput,
    UnsupportedExecution,
    _codex_workflow_status,
)
from agent_bridge_protocol.models import (
    EndpointKind,
    EndpointRef,
    ExecutionOperation,
    ExecutionRequest,
)


def test_codex_workflow_status_only_marks_explicit_leading_blocker() -> None:
    response = "Blocked on GitHub authentication.\nNo files changed."
    assert _codex_workflow_status(response) == "blocked"
    assert _codex_workflow_status("\n## Blocked\nNeed approval") == "succeeded"
    assert _codex_workflow_status("Completed; no blockers remain.") == "succeeded"


async def progress(_summary: str, _percent: float | None) -> None:
    pass


def request(
    operation: ExecutionOperation,
    *,
    adapter: str | None = None,
    conversation_id: str | None = None,
    cwd: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id="exec-1",
        operation=operation,
        instruction="do the thing",
        target=EndpointRef(kind=EndpointKind.NODE, id="node-a"),
        adapter=adapter,
        conversation_id=conversation_id,
        cwd=cwd,
        parameters=parameters or {},
    )


class RecordingRunner:
    def __init__(self, name: str) -> None:
        self.name = name
        self.requests: list[ExecutionRequest] = []

    async def run(
        self,
        item: ExecutionRequest,
        _cancellation: CancellationToken,
        _progress: Any,
    ) -> RunnerOutput:
        self.requests.append(item)
        return RunnerOutput(summary=self.name)


async def test_dispatcher_preserves_all_operation_semantics(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "runner.sqlite3")
    token = CancellationToken(store, "exec-1")
    codex = RecordingRunner("codex")
    wake = RecordingRunner("wake")
    commands = RecordingRunner("command")
    capabilities = RecordingRunner("capability")
    dispatcher = ExecutionDispatcher(
        codex=codex, wake=wake, commands=commands, capabilities=capabilities
    )

    assert (
        await dispatcher.run(request(ExecutionOperation.NEW_EXECUTION), token, progress)
    ).summary == "codex"
    assert (
        await dispatcher.run(
            request(ExecutionOperation.RESUME_CONVERSATION, conversation_id="conversation-1"),
            token,
            progress,
        )
    ).summary == "codex"
    assert (
        await dispatcher.run(request(ExecutionOperation.WAKE_ENDPOINT), token, progress)
    ).summary == "wake"
    assert (
        await dispatcher.run(
            request(ExecutionOperation.INVOKE_ADAPTER, adapter="command"), token, progress
        )
    ).summary == "command"
    assert (
        await dispatcher.run(
            request(ExecutionOperation.INVOKE_ADAPTER, adapter="robot-simulator-e2e"),
            token,
            progress,
        )
    ).summary == "capability"
    assert len(codex.requests) == 2
    store.close()


async def test_codex_runner_prefers_typed_cwd_and_supports_legacy_workspace(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class RecordingCodexClient:
        async def run_turn(
            self,
            *,
            instruction: str,
            conversation_id: str | None,
            workspace: Path | None,
        ) -> RunnerOutput:
            calls.append(
                {
                    "instruction": instruction,
                    "conversation_id": conversation_id,
                    "workspace": workspace,
                }
            )
            return RunnerOutput(summary="complete")

    store = SQLiteExecutionStore(tmp_path / "codex-runner.sqlite3")
    token = CancellationToken(store, "exec-1")
    runner = CodexSDKRunner(RecordingCodexClient())
    typed = tmp_path / "typed"
    legacy = tmp_path / "legacy"

    await runner.run(
        request(
            ExecutionOperation.NEW_EXECUTION,
            cwd=str(typed),
            parameters={"workspace": str(legacy)},
        ),
        token,
        progress,
    )
    await runner.run(
        request(
            ExecutionOperation.RESUME_CONVERSATION,
            conversation_id="thread-1",
            parameters={"workspace": str(legacy)},
        ),
        token,
        progress,
    )

    assert calls[0]["workspace"] == typed
    assert calls[1]["workspace"] == legacy
    assert calls[1]["conversation_id"] == "thread-1"
    store.close()


async def test_allowlisted_command_terminates_on_durable_cancellation(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "cancel.sqlite3")
    runner = AllowlistedCommandRunner(
        {
            "wait": AllowedCommand(
                argv=(sys.executable, "-c", "import time; time.sleep(10)"),
                allowed_workspaces=(tmp_path,),
            )
        }
    )
    run = asyncio.create_task(
        runner.run(
            request(
                ExecutionOperation.INVOKE_ADAPTER,
                adapter="command",
                parameters={"command_id": "wait", "cwd": str(tmp_path)},
            ),
            CancellationToken(store, "exec-1"),
            progress,
        )
    )
    await asyncio.sleep(0.05)
    await store.request_cancellation("exec-1", reason="stop now")
    with pytest.raises(ExecutionCancelled, match="stop now"):
        await asyncio.wait_for(run, timeout=2)
    store.close()


async def test_allowlisted_command_never_uses_shell_and_bounds_workspace(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "runner.sqlite3")
    runner = AllowlistedCommandRunner(
        {
            "python-version": AllowedCommand(
                argv=(sys.executable, "-c", "print('safe')"),
                allowed_workspaces=(tmp_path,),
            )
        }
    )
    output = await runner.run(
        request(
            ExecutionOperation.INVOKE_ADAPTER,
            adapter="command",
            parameters={"command_id": "python-version", "cwd": str(tmp_path)},
        ),
        CancellationToken(store, "exec-1"),
        progress,
    )
    assert output.output["stdout"] == "safe\n"
    with pytest.raises(UnsupportedExecution):
        await runner.run(
            request(
                ExecutionOperation.INVOKE_ADAPTER,
                adapter="command",
                parameters={"command_id": "arbitrary"},
            ),
            CancellationToken(store, "exec-1"),
            progress,
        )
    store.close()


async def test_codex_adapter_resume_keeps_environment_and_denies_foreground_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    class FakeThread:
        id = "thread-1"

        async def run(self, instruction: str) -> Any:
            captured["instruction"] = instruction
            return SimpleNamespace(final_response="finished")

    class FakeCodex:
        async def __aenter__(self) -> FakeCodex:
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        async def thread_resume(self, thread_id: str, **options: Any) -> FakeThread:
            captured["thread_id"] = thread_id
            captured["options"] = options
            return FakeThread()

    fake_sdk = SimpleNamespace(
        AsyncCodex=FakeCodex,
        Sandbox=SimpleNamespace(full_access="full"),
        ApprovalMode=SimpleNamespace(deny_all="deny"),
    )
    real_import = __import__("importlib").import_module

    def fake_import(name: str) -> Any:
        return fake_sdk if name == "openai_codex" else real_import(name)

    monkeypatch.setattr("agent_bridge_bridge.runners.importlib.import_module", fake_import)
    client = OpenAICodexClient(
        model="model-a", config={"sandbox_workspace_write": {"network_access": True}}
    )
    output = await client.run_turn(
        instruction="continue",
        conversation_id="thread-1",
        workspace=tmp_path,
    )
    assert output.output["final_response"] == "finished"
    assert output.output["provider_thread_id"] == "thread-1"
    assert output.output["cwd"] == str(tmp_path)
    assert captured["options"] == {
        "sandbox": "full",
        "approval_mode": "deny",
        "model": "model-a",
        "config": {"sandbox_workspace_write": {"network_access": True}},
        "cwd": str(tmp_path),
    }
