"""Provider lifecycle operations used by the single-user Hub."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Coroutine, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_bridge_providers.codex.app_server import AppServerClient, AppServerError


class ConversationWriterBusy(RuntimeError):
    """The provider currently owns the conversation in another live client."""


class ConversationRuntime:
    def __init__(self, *, codex_bin: str = "codex", claude_bin: str = "claude") -> None:
        self.codex = AppServerClient((codex_bin, "app-server"))
        self.codex.add_notification_handler(self._codex_notification)
        self.claude_bin = claude_bin
        self._turn_locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._codex_waiters: dict[str, asyncio.Future[Mapping[str, Any]]] = {}
        self._codex_completed: dict[str, Mapping[str, Any]] = {}

    async def close(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.codex.close()

    async def start(
        self,
        *,
        provider: str,
        cwd: str,
        prompt: str,
        model: str | None = None,
        effort: str | None = None,
    ) -> str:
        workspace = Path(cwd)
        if not workspace.is_dir():
            raise ValueError(f"working directory is unavailable: {workspace}")
        _validate_effort(provider, effort)
        if provider == "codex":
            thread = await self.codex.start_thread(cwd=str(workspace), model=model)
            thread_id = str(thread["id"])
            self._background(
                self._codex_turn(thread_id, prompt, model=model, effort=effort)
            )
            return thread_id
        if provider == "claude":
            session_id = str(uuid4())
            self._background(
                self._claude_turn(
                    session_id,
                    str(workspace),
                    prompt,
                    new=True,
                    model=model,
                    effort=effort,
                )
            )
            return session_id
        raise ValueError("provider must be codex or claude")

    async def turn(
        self,
        *,
        provider: str,
        provider_thread_id: str,
        cwd: str,
        prompt: str,
        resume: bool = True,
        effort: str | None = None,
    ) -> None:
        _validate_effort(provider, effort)
        lock = self._turn_locks.setdefault(provider_thread_id, asyncio.Lock())
        if lock.locked():
            raise ConversationWriterBusy("conversation already has an active Bridge-delivered turn")
        async with lock:
            if provider == "codex":
                if resume:
                    try:
                        await self.codex.resume_thread(provider_thread_id, cwd=cwd)
                    except AppServerError as exc:
                        if exc.code == -32600 and "already has an active writer" in exc.message:
                            raise ConversationWriterBusy(exc.message) from exc
                        raise
                await self._codex_turn(provider_thread_id, prompt, effort=effort)
                return
            if provider == "claude":
                await self._claude_turn(
                    provider_thread_id.split(":agent:", 1)[0],
                    cwd,
                    prompt,
                    effort=effort,
                )
                return
            raise ValueError("provider must be codex or claude")

    async def _codex_turn(
        self,
        thread_id: str,
        prompt: str,
        *,
        model: str | None = None,
        effort: str | None = None,
    ) -> None:
        turn = await self.codex.start_turn(thread_id, prompt, model=model, effort=effort)
        turn_id = str(turn.get("id") or "")
        if not turn_id:
            raise RuntimeError("Codex App Server returned a turn without an id")
        status = str(turn.get("status") or "")
        if status != "inProgress":
            self._raise_for_codex_turn(turn)
            return
        completed = self._codex_completed.pop(turn_id, None)
        if completed is None:
            waiter = asyncio.get_running_loop().create_future()
            self._codex_waiters[turn_id] = waiter
            try:
                completed = await waiter
            finally:
                self._codex_waiters.pop(turn_id, None)
        self._raise_for_codex_turn(completed)

    async def _codex_notification(self, method: str, params: Mapping[str, Any]) -> None:
        if method != "turn/completed":
            return
        turn = params.get("turn")
        if not isinstance(turn, Mapping):
            return
        turn_id = str(turn.get("id") or "")
        if not turn_id:
            return
        waiter = self._codex_waiters.get(turn_id)
        if waiter is not None and not waiter.done():
            waiter.set_result(turn)
        else:
            self._codex_completed[turn_id] = turn

    @staticmethod
    def _raise_for_codex_turn(turn: Mapping[str, Any]) -> None:
        status = str(turn.get("status") or "")
        if status == "completed":
            return
        error = turn.get("error")
        if isinstance(error, Mapping):
            detail = str(error.get("message") or error)
        else:
            detail = str(error or status or "unknown failure")
        raise RuntimeError(f"Codex turn {status or 'failed'}: {detail}")

    async def _claude_turn(
        self,
        session_id: str,
        cwd: str,
        prompt: str,
        *,
        new: bool = False,
        model: str | None = None,
        effort: str | None = None,
    ) -> None:
        session_args = ["--session-id", session_id] if new else ["--resume", session_id]
        configuration_args: list[str] = []
        if model is not None:
            configuration_args.extend(("--model", model))
        if effort is not None:
            configuration_args.extend(("--effort", effort))
        process = await asyncio.create_subprocess_exec(
            self.claude_bin,
            "--dangerously-skip-permissions",
            *session_args,
            *configuration_args,
            "--print",
            prompt,
            cwd=cwd,
            env=os.environ.copy(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, error = await process.communicate()
        if process.returncode:
            raise RuntimeError(error.decode(errors="replace")[-2000:])

    def _background(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _validate_effort(provider: str, effort: str | None) -> None:
    if provider == "claude" and effort == "ultra":
        raise ValueError("Claude does not support ultra reasoning effort")
