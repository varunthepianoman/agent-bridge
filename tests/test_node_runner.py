from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agent_bridge_node.runner import NativeCommandRunner, NodeCommand


@dataclass
class RecordingLauncher:
    calls: list[list[str]] = field(default_factory=list)

    def launch(self, argv: list[str]) -> None:
        self.calls.append(argv)


def test_resume_is_explicit_and_preserves_workspace(tmp_path: Path) -> None:
    launcher = RecordingLauncher()
    runner = NativeCommandRunner(
        environment_id="wsl-dev", enabled=True, codex_bin="codex", launcher=launcher
    )
    result = runner.execute(
        NodeCommand(
            command_id="cmd-1",
            claim_token="claim-1",
            kind="resume_conversation",
            environment_id="wsl-dev",
            conversation_id="conversation-1",
            provider_thread_id="thread-1",
            workspace=str(tmp_path),
        )
    )
    assert result.status == "succeeded"
    assert result.claim_token == "claim-1"
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
        environment_id="wsl-dev", enabled=True, platform_name="wsl", launcher=launcher
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
        environment_id="host",
        enabled=True,
        claude_bin="claude-custom",
        launcher=launcher,
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


def test_resume_refuses_environment_fallback(tmp_path: Path) -> None:
    launcher = RecordingLauncher()
    runner = NativeCommandRunner(environment_id="host", enabled=True, launcher=launcher)
    result = runner.execute(
        NodeCommand(
            command_id="cmd-2",
            claim_token="claim-2",
            kind="resume_conversation",
            environment_id="devcontainer",
            provider_thread_id="thread-2",
            workspace=str(tmp_path),
        )
    )
    assert result.status == "failed"
    assert "not available" in result.detail
    assert launcher.calls == []


def test_open_path_uses_platform_native_argv(tmp_path: Path) -> None:
    launcher = RecordingLauncher()
    command = NodeCommand(
        command_id="cmd-open",
        claim_token="claim-open",
        kind="open_path",
        environment_id="host",
        path=str(tmp_path),
    )
    windows = NativeCommandRunner(
        environment_id="host", enabled=True, platform_name="windows", launcher=launcher
    )
    assert windows.execute(command).status == "succeeded"
    assert launcher.calls[-1] == ["explorer.exe", str(tmp_path)]

    linux = NativeCommandRunner(
        environment_id="host", enabled=True, platform_name="linux", launcher=launcher
    )
    assert linux.execute(command).status == "succeeded"
    assert launcher.calls[-1] == ["xdg-open", str(tmp_path)]


def test_native_action_failure_is_reported_without_substitute(tmp_path: Path) -> None:
    runner = NativeCommandRunner(environment_id="host", enabled=False)
    command = NodeCommand(
        command_id="cmd-off",
        claim_token="claim-off",
        kind="open_path",
        environment_id="host",
        path=str(tmp_path),
    )
    result = runner.execute(command)
    assert result.status == "failed"
    assert result.launched_command is None


def test_start_codex_passes_launch_model_and_effort(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"thread_id":"thread-new"}\n',
            stderr="",
        )

    monkeypatch.setattr("agent_bridge_node.runner.subprocess.run", run)
    runner = NativeCommandRunner(environment_id="host", enabled=True, codex_bin="codex-custom")
    result = runner.execute(
        NodeCommand(
            command_id="cmd-start-codex",
            claim_token="claim-start-codex",
            kind="start_conversation",
            environment_id="host",
            provider="codex",
            workspace=str(tmp_path),
            prompt="Review the change",
            model="gpt-5.6-sol",
            effort="high",
        )
    )

    assert result.status == "succeeded"
    assert result.output["provider_thread_id"] == "thread-new"
    assert calls == [
        [
            "codex-custom",
            "exec",
            "--json",
            "--model",
            "gpt-5.6-sol",
            "--config",
            'model_reasoning_effort="high"',
            "Review the change",
        ]
    ]


def test_start_and_resume_claude_pass_only_supported_configuration(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="done", stderr="")

    monkeypatch.setattr("agent_bridge_node.runner.subprocess.run", run)
    runner = NativeCommandRunner(environment_id="host", enabled=True, claude_bin="claude-custom")
    started = runner.execute(
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

    assert started.status == "succeeded"
    assert resumed.status == "succeeded"
    assert calls[0][0:2] == ["claude-custom", "--session-id"]
    assert calls[0][3:] == [
        "--model",
        "opus",
        "--effort",
        "high",
        "--print",
        "Review the change",
    ]
    assert calls[1] == [
        "claude-custom",
        "--resume",
        "session-1",
        "--effort",
        "xhigh",
        "--print",
        "Look more deeply",
    ]
