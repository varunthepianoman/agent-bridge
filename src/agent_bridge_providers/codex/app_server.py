"""Async supervisor for the Codex App Server stdio JSONL protocol.

App Server speaks JSON-RPC-like messages without the ``jsonrpc`` field.  Each
message occupies one line on stdin/stdout.  This module deliberately keeps the
wire layer independent of catalog models so it can also support future Codex
operations.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

JsonObject = dict[str, Any]
NotificationHandler = Callable[[str, Mapping[str, Any]], Awaitable[None] | None]


class AppServerError(RuntimeError):
    """An error response returned by Codex App Server."""

    def __init__(
        self,
        code: int | None,
        message: str,
        data: Any = None,
        *,
        method: str | None = None,
    ) -> None:
        super().__init__(f"{method + ': ' if method else ''}{message}")
        self.code = code
        self.message = message
        self.data = data
        self.method = method


class AppServerProtocolError(RuntimeError):
    """The child process emitted a message that violates the wire protocol."""


class AppServerClosedError(RuntimeError):
    """The App Server connection closed before an operation completed."""


@dataclass(frozen=True, slots=True)
class AppServerDiagnostics:
    """A point-in-time, safe-to-display supervisor status snapshot."""

    state: str
    command: tuple[str, ...]
    pid: int | None
    generation: int
    restart_count: int
    pending_requests: int
    malformed_messages: int
    notification_count: int
    last_exit_code: int | None
    initialize_result: Mapping[str, Any] | None
    recent_stderr: tuple[str, ...]


class AppServerClient:
    """Supervise one local ``codex app-server`` stdio process.

    The next request after an unexpected process exit starts a fresh process
    and repeats the initialization handshake.  In-flight requests fail rather
    than being replayed because the generic client cannot know whether a method
    is safe to retry.
    """

    def __init__(
        self,
        command: Sequence[str] = ("codex", "app-server"),
        *,
        client_name: str = "agent_bridge_catalog",
        client_title: str = "Agent Bridge Catalog",
        client_version: str = "0.1.0",
        request_timeout: float = 30.0,
        environment: Mapping[str, str] | None = None,
        notification_queue_size: int = 1_000,
    ) -> None:
        if not command:
            raise ValueError("command must not be empty")
        self._command = tuple(command)
        self._client_info = {
            "name": client_name,
            "title": client_title,
            "version": client_version,
        }
        self._request_timeout = request_timeout
        self._environment = dict(environment) if environment is not None else None
        self._notification_queue: asyncio.Queue[tuple[str, Mapping[str, Any]]] = asyncio.Queue(
            maxsize=notification_queue_size
        )
        self._notification_handlers: list[NotificationHandler] = []
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._write_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._next_id = 0
        self._state = "stopped"
        self._generation = 0
        self._restart_count = 0
        self._malformed_messages = 0
        self._notification_count = 0
        self._last_exit_code: int | None = None
        self._initialize_result: Mapping[str, Any] | None = None
        self._recent_stderr: deque[str] = deque(maxlen=50)
        self._closing = False

    async def __aenter__(self) -> AppServerClient:
        await self.start()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()

    @property
    def is_ready(self) -> bool:
        process = self._process
        return self._state == "ready" and process is not None and process.returncode is None

    def diagnostics(self) -> AppServerDiagnostics:
        process = self._process
        return AppServerDiagnostics(
            state=self._state,
            command=self._command,
            pid=process.pid if process is not None and process.returncode is None else None,
            generation=self._generation,
            restart_count=self._restart_count,
            pending_requests=len(self._pending),
            malformed_messages=self._malformed_messages,
            notification_count=self._notification_count,
            last_exit_code=self._last_exit_code,
            initialize_result=self._initialize_result,
            recent_stderr=tuple(self._recent_stderr),
        )

    def add_notification_handler(self, handler: NotificationHandler) -> None:
        self._notification_handlers.append(handler)

    async def next_notification(
        self, *, timeout: float | None = None
    ) -> tuple[str, Mapping[str, Any]]:
        if timeout is None:
            return await self._notification_queue.get()
        return await asyncio.wait_for(self._notification_queue.get(), timeout)

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self.is_ready:
                return
            await self._stop_process(expected=True)
            await self._spawn_and_initialize(is_restart=self._generation > 0)

    async def restart(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_process(expected=True)
            await self._spawn_and_initialize(is_restart=self._generation > 0)

    async def close(self) -> None:
        async with self._lifecycle_lock:
            self._closing = True
            try:
                await self._stop_process(expected=True)
                self._state = "closed"
            finally:
                self._closing = False

    async def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        await self._ensure_ready()
        await self._send({"method": method, "params": dict(params or {})})

    async def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        await self._ensure_ready()
        return await self._request_on_connection(method, params, timeout=timeout)

    async def list_threads_page(self, **params: Any) -> Mapping[str, Any]:
        result = await self.request("thread/list", params)
        return self._require_mapping(result, "thread/list result")

    async def read_thread(
        self, thread_id: str, *, include_turns: bool = False
    ) -> Mapping[str, Any]:
        result = self._require_mapping(
            await self.request(
                "thread/read",
                {"threadId": thread_id, "includeTurns": include_turns},
            ),
            "thread/read result",
        )
        return self._require_mapping(result.get("thread"), "thread/read result.thread")

    async def start_thread(self, *, cwd: str) -> Mapping[str, Any]:
        result = self._require_mapping(
            await self.request("thread/start", {"cwd": cwd}),
            "thread/start result",
        )
        return self._require_mapping(result.get("thread"), "thread/start result.thread")

    async def resume_thread(self, thread_id: str, *, cwd: str | None = None) -> Mapping[str, Any]:
        params: dict[str, Any] = {"threadId": thread_id}
        if cwd is not None:
            params["cwd"] = cwd
        result = self._require_mapping(
            await self.request("thread/resume", params),
            "thread/resume result",
        )
        return self._require_mapping(result.get("thread"), "thread/resume result.thread")

    async def start_turn(self, thread_id: str, prompt: str) -> Mapping[str, Any]:
        result = self._require_mapping(
            await self.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                },
            ),
            "turn/start result",
        )
        turn = result.get("turn")
        return self._require_mapping(turn, "turn/start result.turn")

    async def _ensure_ready(self) -> None:
        if self.is_ready:
            return
        await self.start()

    async def _spawn_and_initialize(self, *, is_restart: bool) -> None:
        self._state = "starting"
        env = None
        if self._environment is not None:
            env = os.environ.copy()
            env.update(self._environment)
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except Exception:
            self._state = "failed"
            raise
        self._process = process
        self._generation += 1
        if is_restart:
            self._restart_count += 1
        self._reader_task = asyncio.create_task(
            self._read_stdout(process, self._generation),
            name="codex-app-server-stdout",
        )
        self._stderr_task = asyncio.create_task(
            self._read_stderr(process), name="codex-app-server-stderr"
        )
        try:
            result = await self._request_on_connection(
                "initialize", {"clientInfo": self._client_info}
            )
            self._initialize_result = self._require_mapping(result, "initialize result")
            await self._send({"method": "initialized", "params": {}})
        except BaseException:
            await self._stop_process(expected=True)
            self._state = "failed"
            raise
        self._state = "ready"

    async def _request_on_connection(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        loop = asyncio.get_running_loop()
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._send({"method": method, "id": request_id, "params": dict(params or {})})
            return await asyncio.wait_for(
                future, self._request_timeout if timeout is None else timeout
            )
        except TimeoutError as exc:
            raise TimeoutError(f"Codex App Server request timed out: {method}") from exc
        finally:
            self._pending.pop(request_id, None)

    async def _send(self, message: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise AppServerClosedError("Codex App Server is not running")
        encoded = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        async with self._write_lock:
            try:
                process.stdin.write(encoded)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise AppServerClosedError("Codex App Server stdin closed") from exc

    async def _read_stdout(self, process: asyncio.subprocess.Process, generation: int) -> None:
        assert process.stdout is not None
        try:
            while line := await process.stdout.readline():
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("message is not an object")
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    self._malformed_messages += 1
                    self._recent_stderr.append(f"malformed stdout message: {exc}")
                    continue
                await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        finally:
            return_code = await process.wait()
            if generation == self._generation and process is self._process:
                self._last_exit_code = return_code
                if not self._closing and self._state != "stopped":
                    self._state = "failed"
                self._fail_pending(
                    AppServerClosedError(f"Codex App Server exited with status {return_code}")
                )

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        try:
            while line := await process.stderr.readline():
                self._recent_stderr.append(line.decode("utf-8", "replace").rstrip())
        except asyncio.CancelledError:
            raise

    async def _dispatch(self, message: JsonObject) -> None:
        request_id = message.get("id")
        if request_id is not None and "method" not in message:
            future = self._pending.get(request_id)
            if future is None or future.done():
                self._recent_stderr.append(f"response for unknown request id {request_id!r}")
                return
            if "error" in message:
                error = message.get("error")
                if isinstance(error, Mapping):
                    future.set_exception(
                        AppServerError(
                            error.get("code") if isinstance(error.get("code"), int) else None,
                            str(error.get("message", "Unknown App Server error")),
                            error.get("data"),
                        )
                    )
                else:
                    future.set_exception(AppServerProtocolError("invalid error response"))
            elif "result" in message:
                future.set_result(message["result"])
            else:
                future.set_exception(
                    AppServerProtocolError("response has neither result nor error")
                )
            return

        method = message.get("method")
        params = message.get("params", {})
        if not isinstance(method, str) or not isinstance(params, Mapping):
            self._malformed_messages += 1
            self._recent_stderr.append("invalid notification or server request")
            return
        if request_id is not None:
            await self._send(
                {
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Unsupported server request: {method}",
                    },
                }
            )
            return
        await self._publish_notification(method, params)

    async def _publish_notification(self, method: str, params: Mapping[str, Any]) -> None:
        self._notification_count += 1
        notification = (method, dict(params))
        if self._notification_queue.full():
            self._notification_queue.get_nowait()
        self._notification_queue.put_nowait(notification)
        for handler in tuple(self._notification_handlers):
            try:
                result = handler(method, params)
                if result is not None:
                    await result
            except Exception as exc:
                self._recent_stderr.append(f"notification handler failed for {method}: {exc}")

    async def _stop_process(self, *, expected: bool) -> None:
        process = self._process
        if process is None:
            return
        if expected:
            self._state = "stopping"
        if process.stdin is not None:
            process.stdin.close()
        if process.returncode is None:
            try:
                # App Server treats stdin EOF as a normal client disconnect.
                # Give it a chance to perform its own orderly shutdown before
                # sending a signal (which can be unavailable in containers).
                await asyncio.wait_for(process.wait(), 1.0)
            except TimeoutError:
                try:
                    process.terminate()
                except (ProcessLookupError, PermissionError) as exc:
                    self._recent_stderr.append(f"could not terminate App Server process: {exc}")
                try:
                    await asyncio.wait_for(process.wait(), 2.0)
                except TimeoutError:
                    try:
                        process.kill()
                    except (ProcessLookupError, PermissionError) as exc:
                        self._recent_stderr.append(f"could not kill App Server process: {exc}")
                    else:
                        await process.wait()
        self._last_exit_code = process.returncode
        current = asyncio.current_task()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and task is not current:
                with contextlib.suppress(asyncio.CancelledError, AppServerClosedError):
                    await task
        self._fail_pending(AppServerClosedError("Codex App Server stopped"))
        self._process = None
        self._reader_task = None
        self._stderr_task = None
        self._initialize_result = None
        if expected:
            self._state = "stopped"

    def _fail_pending(self, exc: Exception) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(exc)

    @staticmethod
    def _require_mapping(value: Any, description: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise AppServerProtocolError(f"{description} must be an object")
        return value
