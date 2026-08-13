"""Execution adapters with an explicit operation-to-runner boundary."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from agent_bridge_protocol.models import ExecutionOperation, ExecutionRequest

from .execution_store import SQLiteExecutionStore

ProgressCallback = Callable[[str, float | None], Awaitable[None]]
CapabilityHandler = Callable[[ExecutionRequest, "CancellationToken"], Awaitable["RunnerOutput"]]


class RunnerError(RuntimeError):
    retryable = False


class RetryableRunnerError(RunnerError):
    retryable = True


class ExecutionCancelled(RunnerError):
    pass


class UnsupportedExecution(RunnerError):
    pass


@dataclass(frozen=True)
class RunnerOutput:
    summary: str
    output: dict[str, Any] = field(default_factory=dict)
    workflow_status: Literal["succeeded", "blocked"] = "succeeded"


class CancellationToken:
    def __init__(self, store: SQLiteExecutionStore, execution_id: str) -> None:
        self.store = store
        self.execution_id = execution_id

    async def reason(self) -> str | None:
        return await self.store.cancellation_reason(self.execution_id)

    async def raise_if_cancelled(self) -> None:
        reason = await self.reason()
        if reason is not None:
            raise ExecutionCancelled(reason)

    async def wait(self, *, poll_seconds: float = 0.1) -> str:
        while True:
            reason = await self.reason()
            if reason is not None:
                return reason
            await asyncio.sleep(poll_seconds)


class ExecutionRunner(Protocol):
    async def run(
        self,
        request: ExecutionRequest,
        cancellation: CancellationToken,
        progress: ProgressCallback,
    ) -> RunnerOutput: ...


@dataclass(frozen=True)
class AllowedCommand:
    argv: tuple[str, ...]
    allowed_workspaces: tuple[Path, ...] = ()
    timeout_seconds: float = 600.0


class AllowlistedCommandRunner:
    """Runs registered argv prefixes without invoking a shell."""

    def __init__(
        self, commands: Mapping[str, AllowedCommand], *, max_output: int = 200_000
    ) -> None:
        self.commands = dict(commands)
        self.max_output = max_output

    async def run(
        self,
        request: ExecutionRequest,
        cancellation: CancellationToken,
        progress: ProgressCallback,
    ) -> RunnerOutput:
        await cancellation.raise_if_cancelled()
        command_id = request.parameters.get("command_id")
        if not isinstance(command_id, str) or command_id not in self.commands:
            raise UnsupportedExecution("command_id is not allowlisted")
        definition = self.commands[command_id]
        extra_args = request.parameters.get("args", [])
        if not isinstance(extra_args, list) or not all(
            isinstance(item, str) for item in extra_args
        ):
            raise RunnerError("command args must be a list of strings")
        if any("\x00" in item for item in extra_args):
            raise RunnerError("command args cannot contain NUL")
        cwd = self._resolve_workspace(request.parameters.get("cwd"), definition)
        await progress(f"Starting allowlisted command {command_id}", 0)
        process = await asyncio.create_subprocess_exec(
            *definition.argv,
            *extra_args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        communicate = asyncio.create_task(process.communicate())
        cancelled = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {communicate, cancelled},
                timeout=definition.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancelled in done:
                process.terminate()
                await process.wait()
                communicate.cancel()
                raise ExecutionCancelled(cancelled.result())
            if communicate not in done:
                process.kill()
                await process.wait()
                communicate.cancel()
                raise RetryableRunnerError(f"command {command_id!r} timed out")
            stdout, stderr = communicate.result()
        finally:
            cancelled.cancel()
        output = stdout.decode(errors="replace")[-self.max_output :]
        error = stderr.decode(errors="replace")[-self.max_output :]
        if process.returncode != 0:
            raise RetryableRunnerError(
                f"command {command_id!r} exited {process.returncode}: {error[-2000:]}"
            )
        await progress(f"Allowlisted command {command_id} completed", 100)
        return RunnerOutput(
            summary=f"Command {command_id} completed",
            output={"exit_code": process.returncode, "stdout": output, "stderr": error},
        )

    def _resolve_workspace(self, value: object, definition: AllowedCommand) -> str | None:
        if value is None:
            return str(definition.allowed_workspaces[0]) if definition.allowed_workspaces else None
        if not isinstance(value, str):
            raise RunnerError("cwd must be a string")
        candidate = Path(value).resolve()
        roots = [root.resolve() for root in definition.allowed_workspaces]
        if not roots or not any(candidate == root or root in candidate.parents for root in roots):
            raise RunnerError("cwd is outside the command's allowed workspaces")
        return str(candidate)


class ConversationWakeClient(Protocol):
    async def wake(
        self,
        *,
        endpoint_id: str,
        instruction: str,
        conversation_id: str | None,
        parameters: dict[str, Any],
    ) -> dict[str, Any]: ...


class ConversationWakeRunner:
    def __init__(self, client: ConversationWakeClient) -> None:
        self.client = client

    async def run(
        self,
        request: ExecutionRequest,
        cancellation: CancellationToken,
        progress: ProgressCallback,
    ) -> RunnerOutput:
        await cancellation.raise_if_cancelled()
        await progress("Waking registered conversation endpoint", 10)
        response = await self.client.wake(
            endpoint_id=request.target.id,
            instruction=request.instruction,
            conversation_id=request.conversation_id,
            parameters=request.parameters,
        )
        await cancellation.raise_if_cancelled()
        return RunnerOutput(
            summary="Conversation endpoint accepted the wake request", output=response
        )


class CodexTurnClient(Protocol):
    async def run_turn(
        self,
        *,
        instruction: str,
        conversation_id: str | None,
        workspace: Path | None,
    ) -> RunnerOutput: ...


class CodexSDKRunner:
    def __init__(self, client: CodexTurnClient) -> None:
        self.client = client

    async def run(
        self,
        request: ExecutionRequest,
        cancellation: CancellationToken,
        progress: ProgressCallback,
    ) -> RunnerOutput:
        await cancellation.raise_if_cancelled()
        await progress("Starting Codex SDK turn", 5)
        # ``workspace`` was the original free-form parameter. Prefer the typed
        # protocol field while continuing to accept older queued requests.
        workspace_value = request.cwd
        if workspace_value is None:
            workspace_value = request.parameters.get("workspace")
        if workspace_value is not None and not isinstance(workspace_value, str):
            raise RunnerError("workspace must be a string")
        result = await self.client.run_turn(
            instruction=request.instruction,
            conversation_id=request.conversation_id,
            workspace=Path(workspace_value) if workspace_value else None,
        )
        await cancellation.raise_if_cancelled()
        await progress("Codex SDK turn completed", 100)
        return result


class OpenAICodexClient:
    """Optional `openai-codex` adapter; import occurs only when used."""

    def __init__(
        self,
        *,
        model: str | None = None,
        sandbox: str = "full_access",
        approval_mode: str = "deny_all",
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.sandbox = sandbox
        self.approval_mode = approval_mode
        self.config = dict(config or {})

    async def run_turn(
        self,
        *,
        instruction: str,
        conversation_id: str | None,
        workspace: Path | None,
    ) -> RunnerOutput:
        try:
            sdk = importlib.import_module("openai_codex")
        except ImportError as error:
            raise UnsupportedExecution(
                "Codex SDK is unavailable; install the 'codex' optional dependency"
            ) from error
        async_codex: Any = sdk.AsyncCodex
        sandbox = getattr(sdk.Sandbox, self.sandbox)
        approval_mode = getattr(sdk.ApprovalMode, self.approval_mode)
        async with async_codex() as codex:
            options: dict[str, Any] = {
                "sandbox": sandbox,
                "approval_mode": approval_mode,
            }
            if self.config:
                options["config"] = self.config
            if self.model is not None:
                options["model"] = self.model
            if workspace is not None:
                options["cwd"] = str(workspace)
            if conversation_id is not None:
                thread = await codex.thread_resume(conversation_id, **options)
            else:
                thread = await codex.thread_start(**options)
            result = await thread.run(instruction)
            workflow_status = _codex_workflow_status(result.final_response)
            return RunnerOutput(
                summary=(
                    "Codex SDK turn blocked"
                    if workflow_status == "blocked"
                    else "Codex SDK turn completed"
                ),
                output={
                    "final_response": result.final_response,
                    "provider_thread_id": str(thread.id),
                    "cwd": str(workspace) if workspace is not None else None,
                },
                workflow_status=workflow_status,
            )


def _codex_workflow_status(final_response: str) -> Literal["succeeded", "blocked"]:
    """Conservatively recognize an agent-declared workflow blocker.

    Transport success only means the SDK turn completed. A leading ``Blocked``
    declaration is the stable human-readable contract for a turn that could not
    accomplish its requested workflow stage.
    """
    first_line = next(
        (line.strip().casefold() for line in final_response.splitlines() if line.strip()), ""
    )
    return "blocked" if first_line.startswith("blocked") else "succeeded"


class RegisteredCapabilityRunner:
    def __init__(self, capabilities: Mapping[str, CapabilityHandler]) -> None:
        self.capabilities = dict(capabilities)

    async def run(
        self,
        request: ExecutionRequest,
        cancellation: CancellationToken,
        progress: ProgressCallback,
    ) -> RunnerOutput:
        if request.adapter is None or request.adapter not in self.capabilities:
            raise UnsupportedExecution(f"capability {request.adapter!r} is not registered")
        await cancellation.raise_if_cancelled()
        await progress(f"Starting capability {request.adapter}", 0)
        result = await self.capabilities[request.adapter](request, cancellation)
        await cancellation.raise_if_cancelled()
        await progress(f"Capability {request.adapter} completed", 100)
        return result


ROBOT_TEST_CAPABILITY = "robot-simulator-e2e"
SERVER_CLIENT_TEST_CAPABILITY = "server-client-integration"


def registered_test_capabilities(
    *,
    robot_test: CapabilityHandler,
    server_client_test: CapabilityHandler,
) -> dict[str, CapabilityHandler]:
    return {
        ROBOT_TEST_CAPABILITY: robot_test,
        SERVER_CLIENT_TEST_CAPABILITY: server_client_test,
    }


class ExecutionDispatcher:
    """Preserves the semantic distinction between all four execution operations."""

    def __init__(
        self,
        *,
        codex: ExecutionRunner,
        wake: ExecutionRunner,
        commands: ExecutionRunner,
        capabilities: ExecutionRunner,
    ) -> None:
        self.codex = codex
        self.wake = wake
        self.commands = commands
        self.capabilities = capabilities

    async def run(
        self,
        request: ExecutionRequest,
        cancellation: CancellationToken,
        progress: ProgressCallback,
    ) -> RunnerOutput:
        operation = request.operation
        if operation in {ExecutionOperation.NEW_EXECUTION, ExecutionOperation.RESUME_CONVERSATION}:
            return await self.codex.run(request, cancellation, progress)
        if operation == ExecutionOperation.WAKE_ENDPOINT:
            return await self.wake.run(request, cancellation, progress)
        if operation == ExecutionOperation.INVOKE_ADAPTER:
            runner = self.commands if request.adapter == "command" else self.capabilities
            return await runner.run(request, cancellation, progress)
        raise UnsupportedExecution(f"unsupported execution operation {operation!r}")
