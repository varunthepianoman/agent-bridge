from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agent_bridge_node.runner import (
    CommandResult,
    NativeCommandRunner,
    NodeCommand,
    RemoteCommandRunner,
)


@dataclass
class RecordingLauncher:
    calls: list[list[str]] = field(default_factory=list)

    def launch(self, argv: list[str]) -> None:
        self.calls.append(argv)


@dataclass
class FakeCodexRuntime:
    starts: list[NodeCommand] = field(default_factory=list)
    deliveries: list[NodeCommand] = field(default_factory=list)

    async def start(self, request: NodeCommand) -> CommandResult:
        self.starts.append(request)
        return CommandResult(
            command_id=request.command_id,
            claim_token=request.claim_token,
            status="succeeded",
            output={"provider_thread_id": "thread-new"},
        )

    async def deliver(self, request: NodeCommand) -> CommandResult:
        self.deliveries.append(request)
        return CommandResult(
            command_id=request.command_id,
            claim_token=request.claim_token,
            status="succeeded",
        )


def test_resume_is_explicit_and_preserves_workspace(tmp_path: Path) -> None:
    launcher = RecordingLauncher()
    runner = NativeCommandRunner(
        environment_id="host", codex_bin="codex", launcher=launcher
    )
    result = runner.execute(
        NodeCommand(
            command_id="cmd-1",
            claim_token="claim-1",
            kind="resume_conversation",
            environment_id="host",
            conversation_id="conversation-1",
            provider_thread_id="thread-1",
            workspace=str(tmp_path),
        )
    )
    assert result.status == "succeeded"
    expected = [
        "x-terminal-emulator",
        "-e",
        "codex",
        "--dangerously-bypass-approvals-and-sandbox",
        "resume",
        "thread-1",
        "-C",
        str(tmp_path),
    ]
    assert result.launched_command == expected
    assert launcher.calls == [expected]


def test_resume_uses_windows_terminal_for_wsl(tmp_path: Path) -> None:
    launcher = RecordingLauncher()
    runner = NativeCommandRunner(
        environment_id="wsl-dev", platform_name="wsl", launcher=launcher
    )
    result = runner.execute(
        NodeCommand(
            command_id="cmd-wsl",
            claim_token="claim-wsl",
            kind="resume_conversation",
            environment_id="wsl-dev",
            provider_thread_id="thread-wsl",
            workspace=str(tmp_path),
        )
    )
    assert result.status == "succeeded"
    assert launcher.calls[0][:5] == ["wt.exe", "wsl.exe", "--cd", str(tmp_path), "--"]


def test_resume_claude_uses_owning_workspace_and_root_session(tmp_path: Path) -> None:
    launcher = RecordingLauncher()
    runner = NativeCommandRunner(
        environment_id="host", claude_bin="claude-custom", launcher=launcher
    )
    result = runner.execute(
        NodeCommand(
            command_id="cmd-claude",
            claim_token="claim-claude",
            kind="resume_conversation",
            environment_id="host",
            provider="claude",
            provider_thread_id="session-1:agent:review",
            workspace=str(tmp_path),
        )
    )
    assert result.status == "succeeded"
    assert result.launched_command == [
        "x-terminal-emulator",
        "-e",
        "env",
        f"--chdir={tmp_path}",
        "claude-custom",
        "--dangerously-skip-permissions",
        "--resume",
        "session-1",
    ]


def test_open_path_and_codex_url_are_always_permitted(tmp_path: Path) -> None:
    launcher = RecordingLauncher()
    windows = NativeCommandRunner(
        environment_id="windows-native", platform_name="windows", launcher=launcher
    )
    opened = windows.execute(
        NodeCommand(
            command_id="cmd-open",
            claim_token="claim-open",
            kind="open_path",
            environment_id="windows-native",
            path=str(tmp_path),
        )
    )
    native = windows.execute(
        NodeCommand(
            command_id="cmd-url",
            claim_token="claim-url",
            kind="open_native_url",
            environment_id="windows-native",
            native_url="codex://threads/thread-123",
        )
    )
    assert opened.status == "succeeded"
    assert native.status == "succeeded"
    assert launcher.calls == [
        ["explorer.exe", str(tmp_path)],
        ["explorer.exe", "codex://threads/thread-123"],
    ]


def test_native_codex_url_uses_platform_launcher() -> None:
    launcher = RecordingLauncher()
    for platform_name, expected in (
        ("windows", ["explorer.exe", "codex://threads/thread-123"]),
        ("linux", ["xdg-open", "codex://threads/thread-123"]),
        ("wsl", ["xdg-open", "codex://threads/thread-123"]),
        ("darwin", ["open", "codex://threads/thread-123"]),
    ):
        result = NativeCommandRunner(
            environment_id="host", platform_name=platform_name, launcher=launcher
        ).execute(
            NodeCommand(
                command_id=f"cmd-{platform_name}",
                claim_token=f"claim-{platform_name}",
                kind="open_native_url",
                environment_id="host",
                native_url="codex://threads/thread-123",
            )
        )
        assert result.status == "succeeded"
        assert launcher.calls[-1] == expected


def test_native_url_preserves_scheme_and_unknown_platform_validation() -> None:
    launcher = RecordingLauncher()
    linux = NativeCommandRunner(
        environment_id="host", platform_name="linux", launcher=launcher
    )
    wrong_scheme = linux.execute(
        NodeCommand(
            command_id="cmd-scheme",
            claim_token="claim-scheme",
            kind="open_native_url",
            environment_id="host",
            native_url="https://example.test",
        )
    )
    wrong_platform = NativeCommandRunner(
        environment_id="host", platform_name="plan9", launcher=launcher
    ).execute(
        NodeCommand(
            command_id="cmd-platform",
            claim_token="claim-platform",
            kind="open_native_url",
            environment_id="host",
            native_url="codex://threads/thread-123",
        )
    )
    assert wrong_scheme.status == "failed"
    assert wrong_platform.status == "failed"
    assert launcher.calls == []


async def test_remote_runner_routes_all_codex_turns_through_runtime(tmp_path: Path) -> None:
    runtime = FakeCodexRuntime()
    native = NativeCommandRunner(environment_id="host")
    runner = RemoteCommandRunner(native, runtime)
    started = await runner.execute(
        NodeCommand(
            command_id="cmd-start",
            claim_token="claim-start",
            kind="start_conversation",
            environment_id="host",
            provider="codex",
            workspace=str(tmp_path),
            prompt="Start",
            model="gpt-5.6-terra",
            effort="medium",
        )
    )
    delivered = await runner.execute(
        NodeCommand(
            command_id="cmd-turn",
            claim_token="claim-turn",
            kind="deliver_turn",
            environment_id="host",
            provider="codex",
            provider_thread_id="thread-new",
            workspace=str(tmp_path),
            prompt="Continue",
        )
    )
    assert started.status == "succeeded"
    assert delivered.status == "succeeded"
    assert [item.command_id for item in runtime.starts] == ["cmd-start"]
    assert [item.command_id for item in runtime.deliveries] == ["cmd-turn"]


async def test_remote_runner_rejects_missing_codex_workspace_without_fallback() -> None:
    runtime = FakeCodexRuntime()
    runner = RemoteCommandRunner(NativeCommandRunner(environment_id="host"), runtime)
    result = await runner.execute(
        NodeCommand(
            command_id="cmd-start",
            claim_token="claim-start",
            kind="start_conversation",
            environment_id="host",
            workspace="/does/not/exist",
            prompt="Start",
        )
    )
    assert result.status == "failed"
    assert runtime.starts == []


async def test_remote_runner_routes_claude_start_through_runtime(tmp_path: Path) -> None:
    codex = FakeCodexRuntime()
    claude = FakeCodexRuntime()
    runner = RemoteCommandRunner(
        NativeCommandRunner(environment_id="host"), codex, claude
    )
    result = await runner.execute(
        NodeCommand(
            command_id="cmd-start-claude",
            claim_token="claim-start-claude",
            kind="start_conversation",
            environment_id="host",
            provider="claude",
            workspace=str(tmp_path),
            prompt="Review the change",
            model="opus",
            effort="high",
        )
    )

    assert result.status == "succeeded"
    assert [item.command_id for item in claude.starts] == ["cmd-start-claude"]
    assert codex.starts == []


def test_deliver_claude_preserves_configuration(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="done", stderr="")

    monkeypatch.setattr("agent_bridge_node.runner.subprocess.run", run)
    runner = NativeCommandRunner(environment_id="host", claude_bin="claude-custom")
    resumed = runner.execute(
        NodeCommand(
            command_id="cmd-turn-claude",
            claim_token="claim-turn-claude",
            kind="deliver_turn",
            environment_id="host",
            provider="claude",
            provider_thread_id="session-1",
            workspace=str(tmp_path),
            prompt="Look more deeply",
            effort="xhigh",
        )
    )
    assert resumed.status == "succeeded"
    assert calls[0][0] == [
        "claude-custom",
        "--resume",
        "session-1",
        "--effort",
        "xhigh",
        "--print",
        "Look more deeply",
    ]
    assert calls[0][1]["encoding"] == "utf-8"
    assert calls[0][1]["errors"] == "replace"


def test_claude_failure_tolerates_missing_subprocess_streams(monkeypatch, tmp_path: Path) -> None:
    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, stdout=None, stderr=None)

    monkeypatch.setattr("agent_bridge_node.runner.subprocess.run", run)
    result = NativeCommandRunner(environment_id="host").execute(
        NodeCommand(
            command_id="cmd-no-streams",
            claim_token="claim-no-streams",
            kind="deliver_turn",
            environment_id="host",
            provider="claude",
            provider_thread_id="session-existing",
            workspace=str(tmp_path),
            prompt="hi",
        )
    )
    assert result.status == "failed"
    assert result.detail == "provider turn failed"
