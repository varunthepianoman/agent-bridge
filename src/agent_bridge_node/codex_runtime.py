"""Remote Codex turn lifecycle backed by one supervised App Server."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import Any, Literal

from agent_bridge_providers.codex import (
    AppServerClient,
    AppServerClosedError,
    AppServerError,
    CodexCatalogAdapter,
)

from .runner import CommandResult, NodeCommand, NodeTurnEvent

LOGGER = logging.getLogger(__name__)
TurnEventSink = Callable[[NodeTurnEvent], Awaitable[None] | None]


class RemoteCodexRuntime:
    def __init__(
        self,
        client: AppServerClient,
        *,
        node_id: str,
        event_sink: TurnEventSink | None = None,
    ) -> None:
        self.client = client
        self.node_id = node_id
        self.event_sink = event_sink
        self._waiters: dict[str, asyncio.Future[Mapping[str, Any]]] = {}
        self._completed: dict[str, Mapping[str, Any]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        client.add_notification_handler(self._notification)
        client.add_close_handler(self._closed)

    def set_event_sink(self, event_sink: TurnEventSink) -> None:
        self.event_sink = event_sink

    async def wait_for_background(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks))

    async def close(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def start(self, request: NodeCommand) -> CommandResult:
        assert request.workspace is not None
        assert request.prompt is not None
        thread_id: str | None = None
        try:
            thread = await self.client.start_thread(cwd=request.workspace, model=request.model)
            thread_id = self._required_id(thread, "thread/start")
            turn = await self.client.start_turn(
                thread_id,
                request.prompt,
                model=request.model,
                effort=request.effort,
            )
            turn_id = self._required_id(turn, "turn/start")
        except Exception as error:
            if thread_id is not None:
                await self._release(thread_id)
            return self._failure(request, error)
        self._background(self._observe_initial_turn(request, thread_id, turn_id, turn))
        return CommandResult(
            command_id=request.command_id,
            claim_token=request.claim_token,
            status="succeeded",
            detail="Provider conversation accepted its initial turn",
            output={
                "provider_thread_id": thread_id,
                "provider_turn_id": turn_id,
                "initial_turn_status": "inProgress",
            },
        )

    async def deliver(self, request: NodeCommand) -> CommandResult:
        assert request.provider_thread_id is not None
        assert request.workspace is not None
        assert request.prompt is not None
        owns_thread = False
        try:
            await self.client.resume_thread(
                request.provider_thread_id,
                cwd=request.workspace,
            )
            owns_thread = True
            turn = await self.client.start_turn(
                request.provider_thread_id,
                request.prompt,
                effort=request.effort,
            )
            turn_id = self._required_id(turn, "turn/start")
            completed = await self._wait_for_turn(turn_id, turn)
            self._raise_for_turn(completed)
            return CommandResult(
                command_id=request.command_id,
                claim_token=request.claim_token,
                status="succeeded",
                detail="Bridge message delivered as a provider user turn",
                output={
                    "message_id": request.message_id,
                    "correlation_id": request.correlation_id,
                    "provider_turn_id": turn_id,
                },
            )
        except AppServerError as error:
            status: Literal["blocked", "failed"] = (
                "blocked"
                if error.code == -32600 and "active writer" in error.message.casefold()
                else "failed"
            )
            return self._failure(request, error, status=status)
        except Exception as error:
            return self._failure(request, error)
        finally:
            if owns_thread:
                await self._release(request.provider_thread_id)

    async def read(self, request: NodeCommand) -> CommandResult:
        """Read a stored thread projection without acquiring its provider writer."""

        assert request.provider_thread_id is not None
        try:
            thread = await self.client.read_thread(
                request.provider_thread_id,
                include_turns=True,
            )
            conversation = CodexCatalogAdapter.map_thread(thread)
        except Exception as error:
            return self._failure(request, error)
        return CommandResult(
            command_id=request.command_id,
            claim_token=request.claim_token,
            status="succeeded",
            detail="Codex conversation read without acquiring its writer",
            output={
                "node_id": self.node_id,
                "environment_id": request.environment_id,
                "provider": conversation.provider,
                "provider_thread_id": conversation.provider_thread_id,
                "conversation": {
                    "provider": conversation.provider,
                    "provider_thread_id": conversation.provider_thread_id,
                    "title": conversation.title,
                    "preview": conversation.preview,
                    "cwd": conversation.cwd,
                    "source_kind": conversation.source_kind,
                    "model_provider": conversation.model_provider,
                    "created_at": conversation.created_at,
                    "updated_at": conversation.updated_at,
                    "status": conversation.status,
                    "parent_thread_id": conversation.parent_thread_id,
                    "git_sha": conversation.git_sha,
                    "git_branch": conversation.git_branch,
                    "git_origin_url": conversation.git_origin_url,
                    "is_pinned": conversation.is_pinned,
                    "is_ephemeral": conversation.is_ephemeral,
                    "transcript_text": conversation.transcript_text,
                    "last_assistant_message": conversation.last_assistant_message,
                },
            },
        )

    async def _observe_initial_turn(
        self,
        request: NodeCommand,
        thread_id: str,
        turn_id: str,
        turn: Mapping[str, Any],
    ) -> None:
        status: Literal["completed", "failed", "interrupted"] = "failed"
        detail: str | None = None
        try:
            completed = await self._wait_for_turn(turn_id, turn)
            provider_status = str(completed.get("status") or "failed")
            if provider_status == "completed":
                status = "completed"
            elif provider_status == "interrupted":
                status = "interrupted"
            else:
                status = "failed"
            if status == "failed":
                detail = self._turn_error(completed)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            detail = self._safe_error(error)
        finally:
            await self._release(thread_id)
        event = NodeTurnEvent(
            event_id=f"{self.node_id}/{thread_id}/{turn_id}/{status}",
            environment_id=request.environment_id,
            provider="codex",
            provider_thread_id=thread_id,
            provider_turn_id=turn_id,
            command_id=request.command_id,
            status=status,
            detail=detail,
        )
        if self.event_sink is None:
            raise RuntimeError("Codex turn event sink is not configured")
        result = self.event_sink(event)
        if result is not None:
            await result

    async def _wait_for_turn(
        self, turn_id: str, turn: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if str(turn.get("status") or "") != "inProgress":
            return turn
        completed = self._completed.pop(turn_id, None)
        if completed is not None:
            return completed
        waiter: asyncio.Future[Mapping[str, Any]] = asyncio.get_running_loop().create_future()
        self._waiters[turn_id] = waiter
        try:
            return await waiter
        finally:
            self._waiters.pop(turn_id, None)

    async def _notification(self, method: str, params: Mapping[str, Any]) -> None:
        if method != "turn/completed":
            return
        turn = params.get("turn")
        if not isinstance(turn, Mapping):
            return
        turn_id = str(turn.get("id") or "")
        if not turn_id:
            return
        waiter = self._waiters.get(turn_id)
        if waiter is not None and not waiter.done():
            waiter.set_result(turn)
        else:
            self._completed[turn_id] = turn

    async def _closed(self, error: AppServerClosedError) -> None:
        for waiter in tuple(self._waiters.values()):
            if not waiter.done():
                waiter.set_exception(error)

    async def _release(self, thread_id: str) -> None:
        if not self.client.is_ready:
            LOGGER.warning(
                "cannot release Codex writer for thread %s because App Server is unavailable",
                thread_id,
            )
            return
        try:
            await self.client.unsubscribe_thread(thread_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("failed to release Codex writer for thread %s", thread_id)

    def _background(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task: asyncio.Task[None] = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._background_done)

    def _background_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            LOGGER.error(
                "Codex initial-turn observer failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    @staticmethod
    def _required_id(value: Mapping[str, Any], operation: str) -> str:
        identifier = str(value.get("id") or "")
        if not identifier:
            raise RuntimeError(f"Codex App Server {operation} returned no id")
        return identifier

    @classmethod
    def _raise_for_turn(cls, turn: Mapping[str, Any]) -> None:
        status = str(turn.get("status") or "")
        if status == "completed":
            return
        raise RuntimeError(cls._turn_error(turn) or f"Codex turn {status or 'failed'}")

    @staticmethod
    def _turn_error(turn: Mapping[str, Any]) -> str | None:
        error = turn.get("error")
        if isinstance(error, Mapping):
            return str(error.get("message") or error)
        return str(error) if error else None

    @staticmethod
    def _safe_error(error: Exception) -> str:
        if isinstance(error, AppServerError):
            return error.message
        return str(error) or type(error).__name__

    @classmethod
    def _failure(
        cls,
        request: NodeCommand,
        error: Exception,
        *,
        status: Literal["blocked", "failed"] = "failed",
    ) -> CommandResult:
        return CommandResult(
            command_id=request.command_id,
            claim_token=request.claim_token,
            status=status,
            detail=cls._safe_error(error),
            output={
                "message_id": request.message_id,
                "correlation_id": request.correlation_id,
            },
        )
