"""Remote Claude initial-turn lifecycle backed by a supervised CLI process."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from .runner import CommandResult, NodeCommand, NodeTurnEvent

LOGGER = logging.getLogger(__name__)
TurnEventSink = Callable[[NodeTurnEvent], Awaitable[None] | None]


class ClaudeProcess(Protocol):
    @property
    def returncode(self) -> int | None: ...

    async def communicate(self) -> tuple[bytes, bytes]: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


ProcessStarter = Callable[[list[str], Path], Awaitable[ClaudeProcess]]


class RemoteClaudeRuntime:
    def __init__(
        self,
        claude_bin: str,
        *,
        node_id: str,
        event_sink: TurnEventSink | None = None,
        process_starter: ProcessStarter | None = None,
    ) -> None:
        self.claude_bin = claude_bin
        self.node_id = node_id
        self.event_sink = event_sink
        self.process_starter = process_starter or self._start_process
        self._tasks: set[asyncio.Task[None]] = set()
        self._processes: dict[int, ClaudeProcess] = {}

    def set_event_sink(self, event_sink: TurnEventSink) -> None:
        self.event_sink = event_sink

    async def wait_for_background(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks))

    async def close(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        if self._processes:
            await asyncio.gather(*(self._stop(process) for process in self._processes.values()))
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def start(self, request: NodeCommand) -> CommandResult:
        assert request.workspace is not None
        assert request.prompt is not None
        session_id = str(uuid4())
        turn_id = f"turn-{uuid4()}"
        argv = [self.claude_bin, "--session-id", session_id]
        if request.model:
            argv.extend(("--model", request.model))
        if request.effort:
            argv.extend(("--effort", request.effort))
        argv.extend(("--print", request.prompt))
        try:
            process = await self.process_starter(argv, Path(request.workspace))
        except OSError as error:
            return self._failure(request, f"provider agent failed to start: {error}")
        self._processes[id(process)] = process
        self._background(self._observe_initial_turn(request, session_id, turn_id, process))
        return CommandResult(
            command_id=request.command_id,
            claim_token=request.claim_token,
            status="succeeded",
            detail="Provider conversation accepted its initial turn",
            output={
                "provider_thread_id": session_id,
                "provider_turn_id": turn_id,
                "initial_turn_status": "inProgress",
            },
        )

    async def _observe_initial_turn(
        self,
        request: NodeCommand,
        session_id: str,
        turn_id: str,
        process: ClaudeProcess,
    ) -> None:
        try:
            try:
                stdout, stderr = await process.communicate()
            except asyncio.CancelledError:
                await self._stop(process)
                raise
        finally:
            self._processes.pop(id(process), None)
        status: Literal["completed", "failed"] = (
            "completed" if process.returncode == 0 else "failed"
        )
        detail = None
        if status == "failed":
            detail = self._safe_output(stderr or stdout) or "provider agent failed"
        event = NodeTurnEvent(
            event_id=f"{self.node_id}/{session_id}/{turn_id}/{status}",
            environment_id=request.environment_id,
            provider="claude",
            provider_thread_id=session_id,
            provider_turn_id=turn_id,
            command_id=request.command_id,
            status=status,
            detail=detail,
        )
        if self.event_sink is None:
            raise RuntimeError("Claude turn event sink is not configured")
        result = self.event_sink(event)
        if result is not None:
            await result

    async def _start_process(self, argv: list[str], workspace: Path) -> ClaudeProcess:
        return await asyncio.create_subprocess_exec(
            *argv,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    @staticmethod
    async def _stop(process: ClaudeProcess) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()

    def _background(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._background_done)

    def _background_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            LOGGER.error(
                "Claude initial-turn observer failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    @staticmethod
    def _safe_output(value: bytes) -> str:
        return value.decode("utf-8", errors="replace")[-2_000:]

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
