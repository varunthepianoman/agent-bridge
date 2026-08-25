"""Explicit native actions requested by the Catalog hub."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class NodeCommand(BaseModel):
    model_config = ConfigDict(extra="allow")

    command_id: str = Field(min_length=1)
    claim_token: str = Field(min_length=1)
    kind: Literal[
        "resume_conversation",
        "open_path",
        "open_native_url",
        "deliver_turn",
        "start_conversation",
    ]
    environment_id: str = Field(min_length=1)
    conversation_id: str | None = None
    provider: str = "codex"
    provider_thread_id: str | None = None
    workspace: str | None = None
    path: str | None = None
    native_url: str | None = None
    prompt: str | None = None
    message_id: str | None = None
    correlation_id: str | None = None
    model: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max", "ultra"] | None = None


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    claim_token: str
    status: Literal["succeeded", "blocked", "failed", "cancelled"]
    detail: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    launched_command: list[str] | None = Field(default=None, exclude=True)


class NodeTurnEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    provider: Literal["codex"] = "codex"
    provider_thread_id: str = Field(min_length=1)
    provider_turn_id: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
    status: Literal["completed", "failed", "interrupted"]
    detail: str | None = None


class CodexRuntime(Protocol):
    async def start(self, request: NodeCommand) -> CommandResult: ...

    async def deliver(self, request: NodeCommand) -> CommandResult: ...


class ProcessLauncher(Protocol):
    def launch(self, argv: list[str]) -> None: ...


class SystemProcessLauncher:
    def launch(self, argv: list[str]) -> None:
        subprocess.Popen(argv, start_new_session=os.name != "nt")


class NativeCommandRunner:
    def __init__(
        self,
        *,
        environment_id: str,
        codex_bin: str = "codex",
        claude_bin: str = "claude",
        platform_name: str | None = None,
        launcher: ProcessLauncher | None = None,
    ) -> None:
        self.environment_id = environment_id
        self.codex_bin = codex_bin
        self.claude_bin = claude_bin
        self.platform_name = platform_name or ("windows" if os.name == "nt" else "linux")
        self.launcher = launcher or SystemProcessLauncher()

    def execute(self, request: NodeCommand) -> CommandResult:
        if request.environment_id != self.environment_id:
            return self._failure(
                request, f"environment {request.environment_id!r} is not available on this node"
            )
        if request.kind == "deliver_turn":
            return self._deliver_turn(request)
        if request.kind == "start_conversation":
            return self._start_conversation(request)
        if request.kind == "resume_conversation":
            return self._resume(request)
        if request.kind == "open_native_url":
            return self._open_native_url(request)
        return self._open(request)

    def _start_conversation(self, request: NodeCommand) -> CommandResult:
        if request.provider not in {"codex", "claude"}:
            return self._failure(request, f"provider {request.provider!r} is not supported")
        if request.provider == "claude" and request.effort == "ultra":
            return self._failure(request, "Claude does not support ultra reasoning effort")
        if not request.workspace or not request.prompt:
            return self._failure(request, "agent start is missing a workspace or prompt")
        workspace = Path(request.workspace)
        if not workspace.is_dir():
            return self._failure(request, f"workspace is unavailable: {workspace}")
        if request.provider == "codex":
            return self._failure(request, "Codex start requires the App Server runner")
        session_id = str(uuid4())
        argv = [self.claude_bin, "--session-id", session_id]
        if request.model:
            argv.extend(("--model", request.model))
        if request.effort:
            argv.extend(("--effort", request.effort))
        argv.extend(("--print", request.prompt))
        try:
            completed = subprocess.run(
                argv,
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=24 * 60 * 60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return self._failure(request, f"provider agent failed to start: {error}")
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode:
            detail = (stderr or stdout)[-2_000:]
            return self._failure(request, detail or "provider agent failed")
        return CommandResult(
            command_id=request.command_id,
            claim_token=request.claim_token,
            status="succeeded",
            detail="Provider conversation completed its initial turn",
            output={"provider_thread_id": session_id},
        )

    def _deliver_turn(self, request: NodeCommand) -> CommandResult:
        if request.provider not in {"codex", "claude"}:
            return self._failure(request, f"provider {request.provider!r} is not supported")
        if request.provider == "claude" and request.effort == "ultra":
            return self._failure(request, "Claude does not support ultra reasoning effort")
        if not request.provider_thread_id or not request.workspace or not request.prompt:
            return self._failure(request, "turn delivery is missing a thread, workspace, or prompt")
        workspace = Path(request.workspace)
        if not workspace.is_dir():
            return self._failure(request, f"workspace is unavailable: {workspace}")
        if request.provider == "codex":
            return self._failure(request, "Codex turn delivery requires the App Server runner")
        session_id = request.provider_thread_id.split(":agent:", 1)[0]
        argv = [self.claude_bin, "--resume", session_id]
        if request.effort:
            argv.extend(("--effort", request.effort))
        argv.extend(("--print", request.prompt))
        try:
            completed = subprocess.run(
                argv,
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=24 * 60 * 60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return self._failure(request, f"provider turn failed to start: {error}")
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode:
            detail = (stderr or stdout)[-2_000:]
            return self._failure(request, detail or "provider turn failed")
        return CommandResult(
            command_id=request.command_id,
            claim_token=request.claim_token,
            status="succeeded",
            detail="Bridge message delivered as a provider user turn",
            output={
                "message_id": request.message_id,
                "correlation_id": request.correlation_id,
            },
        )

    def _resume(self, request: NodeCommand) -> CommandResult:
        if request.provider not in {"codex", "claude"}:
            return self._failure(request, f"provider {request.provider!r} is not supported")
        if not request.provider_thread_id:
            return self._failure(request, "resume command has no provider thread id")
        if not request.workspace:
            return self._failure(request, "resume command has no workspace")
        workspace = Path(request.workspace)
        if not workspace.is_dir():
            return self._failure(request, f"workspace is unavailable: {workspace}")
        if request.provider == "claude":
            session_id = request.provider_thread_id.split(":agent:", 1)[0]
            provider_argv = [
                self.claude_bin,
                "--dangerously-skip-permissions",
                "--resume",
                session_id,
            ]
        else:
            provider_argv = [
                self.codex_bin,
                "--dangerously-bypass-approvals-and-sandbox",
                "resume",
                request.provider_thread_id,
                "-C",
                str(workspace),
            ]
        platform = self.platform_name.casefold()
        if platform.startswith("win"):
            argv = ["wt.exe", "-d", str(workspace), *provider_argv]
        elif platform == "wsl":
            argv = ["wt.exe", "wsl.exe", "--cd", str(workspace), "--", *provider_argv]
        elif platform == "linux":
            if request.provider == "claude":
                argv = [
                    "x-terminal-emulator",
                    "-e",
                    "env",
                    f"--chdir={workspace}",
                    *provider_argv,
                ]
            else:
                argv = ["x-terminal-emulator", "-e", *provider_argv]
        else:
            return self._failure(request, f"platform {self.platform_name!r} is unsupported")
        return self._launch(request, argv, f"{request.provider.title()} conversation launched")

    def _open(self, request: NodeCommand) -> CommandResult:
        if not request.path:
            return self._failure(request, "open command has no path")
        target = Path(request.path)
        if not target.exists():
            return self._failure(request, f"path is unavailable: {target}")
        if self.platform_name.casefold().startswith("win"):
            argv = ["explorer.exe", str(target)]
        elif self.platform_name.casefold() in {"linux", "wsl"}:
            argv = ["xdg-open", str(target)]
        else:
            return self._failure(request, f"platform {self.platform_name!r} is unsupported")
        return self._launch(request, argv, "Path opened")

    def _open_native_url(self, request: NodeCommand) -> CommandResult:
        if not request.native_url:
            return self._failure(request, "native URL command has no URL")
        parsed = urlparse(request.native_url)
        if parsed.scheme != "codex":
            return self._failure(request, "only Codex native URLs are supported")
        if not self.platform_name.casefold().startswith("win"):
            return self._failure(request, "native Codex URLs are supported only on Windows")
        return self._launch(
            request,
            ["explorer.exe", request.native_url],
            "Codex opened in the desktop app",
        )

    def _launch(self, request: NodeCommand, argv: list[str], detail: str) -> CommandResult:
        try:
            self.launcher.launch(argv)
        except OSError as error:
            return self._failure(request, f"native launch failed: {error}")
        return CommandResult(
            command_id=request.command_id,
            claim_token=request.claim_token,
            status="succeeded",
            detail=detail,
            output={"launched": True},
            launched_command=argv,
        )

    @staticmethod
    def _failure(request: NodeCommand, detail: str) -> CommandResult:
        return CommandResult(
            command_id=request.command_id,
            claim_token=request.claim_token,
            status="failed",
            detail=detail,
            output={
                "message_id": request.message_id,
                "correlation_id": request.correlation_id,
            },
        )


class RemoteCommandRunner:
    """Route Codex commands asynchronously and keep native/Claude work off-loop."""

    def __init__(
        self,
        native: NativeCommandRunner,
        codex: CodexRuntime,
    ) -> None:
        self.native = native
        self.codex = codex

    async def execute(self, request: NodeCommand) -> CommandResult:
        if request.environment_id != self.native.environment_id:
            return NativeCommandRunner._failure(
                request,
                f"environment {request.environment_id!r} is not available on this node",
            )
        if request.provider == "codex" and request.kind in {
            "start_conversation",
            "deliver_turn",
        }:
            validation = self._validate_codex(request)
            if validation is not None:
                return validation
            if request.kind == "start_conversation":
                return await self.codex.start(request)
            return await self.codex.deliver(request)
        return await asyncio.to_thread(self.native.execute, request)

    @staticmethod
    def _validate_codex(request: NodeCommand) -> CommandResult | None:
        if not request.workspace or not request.prompt:
            return NativeCommandRunner._failure(
                request,
                "Codex command is missing a workspace or prompt",
            )
        if not Path(request.workspace).is_dir():
            return NativeCommandRunner._failure(
                request,
                f"workspace is unavailable: {request.workspace}",
            )
        if request.kind == "deliver_turn" and not request.provider_thread_id:
            return NativeCommandRunner._failure(request, "Codex turn delivery is missing a thread")
        return None
