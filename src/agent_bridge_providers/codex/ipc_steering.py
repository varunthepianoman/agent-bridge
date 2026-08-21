"""Guarded client for Codex desktop's versioned same-user IPC steering route."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
import struct
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_bridge_providers.active_turn import (
    ActiveTurnDeliveryResult,
    ActiveTurnDeliveryState,
)

_FRAME_HEADER = struct.Struct("<I")
_MAX_FRAME_BYTES = 16 * 1024 * 1024
_PROTOCOL_VERSION = 1


class CodexIpcSteering:
    """Deliver one message to the native client that owns a local Codex thread."""

    def __init__(
        self,
        *,
        socket_path: Path | None = None,
        request_timeout_seconds: float = 10.0,
        host_id: str = "local",
    ) -> None:
        self.socket_path = socket_path or Path.home() / ".codex" / "ipc" / "ipc.sock"
        self.request_timeout_seconds = request_timeout_seconds
        self.host_id = host_id

    async def deliver(
        self,
        *,
        provider_thread_id: str,
        cwd: str,
        prompt: str,
        message_id: str,
    ) -> ActiveTurnDeliveryResult:
        try:
            self._validate_socket()
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path),
                timeout=self.request_timeout_seconds,
            )
        except (OSError, TimeoutError, ValueError) as exc:
            return ActiveTurnDeliveryResult(
                ActiveTurnDeliveryState.UNAVAILABLE,
                f"Codex IPC is unavailable: {exc}",
            )

        try:
            initialized = await self._request(
                reader,
                writer,
                method="initialize",
                params={"clientType": "agent-bridge"},
                version=None,
            )
            client_id = _nested_text(initialized, "result", "clientId")
            if not client_id:
                return ActiveTurnDeliveryResult(
                    ActiveTurnDeliveryState.UNAVAILABLE,
                    "Codex IPC initialization did not return a client id",
                )
            owner = await self._request(
                reader,
                writer,
                method="thread-owner-discovery",
                params={"hostId": self.host_id, "conversationId": provider_thread_id},
                version=_PROTOCOL_VERSION,
                source_client_id=client_id,
            )
            if owner.get("resultType") == "error":
                return ActiveTurnDeliveryResult(
                    ActiveTurnDeliveryState.UNAVAILABLE,
                    f"Codex thread owner is unavailable: {owner.get('error', 'unknown error')}",
                )
            owner_id = owner.get("handledByClientId")
            if not isinstance(owner_id, str) or not owner_id:
                return ActiveTurnDeliveryResult(
                    ActiveTurnDeliveryState.UNAVAILABLE,
                    "Codex IPC did not identify a thread owner",
                )

            params = _steer_params(
                provider_thread_id=provider_thread_id,
                cwd=cwd,
                prompt=prompt,
                message_id=message_id,
            )
            try:
                steered = await self._request(
                    reader,
                    writer,
                    method="thread-follower-steer-turn",
                    params=params,
                    version=_PROTOCOL_VERSION,
                    source_client_id=client_id,
                    target_client_id=owner_id,
                    dispatched_is_ambiguous=True,
                )
            except _DispatchedRequestError as exc:
                return ActiveTurnDeliveryResult(ActiveTurnDeliveryState.UNCERTAIN, str(exc))
            if steered.get("resultType") == "error":
                return ActiveTurnDeliveryResult(
                    ActiveTurnDeliveryState.UNAVAILABLE,
                    f"Codex rejected active-turn steering: {steered.get('error', 'unknown error')}",
                )
            return ActiveTurnDeliveryResult(ActiveTurnDeliveryState.DELIVERED)
        except (_IpcProtocolError, OSError, TimeoutError) as exc:
            return ActiveTurnDeliveryResult(ActiveTurnDeliveryState.UNAVAILABLE, str(exc))
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    def _validate_socket(self) -> None:
        if os.name == "nt":
            raise ValueError("local Codex IPC steering currently supports Unix sockets only")
        parent = self.socket_path.parent
        parent_info = parent.lstat()
        socket_info = self.socket_path.lstat()
        expected_uid = os.getuid()
        if parent.is_symlink() or self.socket_path.is_symlink():
            raise ValueError("Codex IPC path must not contain a symlink endpoint")
        if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != expected_uid:
            raise ValueError("Codex IPC directory is not owned by the current user")
        if parent_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError("Codex IPC directory is writable by another user")
        if not stat.S_ISSOCK(socket_info.st_mode) or socket_info.st_uid != expected_uid:
            raise ValueError("Codex IPC socket is not owned by the current user")

    async def _request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        method: str,
        params: Mapping[str, Any],
        version: int | None,
        source_client_id: str | None = None,
        target_client_id: str | None = None,
        dispatched_is_ambiguous: bool = False,
    ) -> Mapping[str, Any]:
        request_id = str(uuid4())
        request: dict[str, Any] = {
            "type": "request",
            "requestId": request_id,
            "method": method,
            "params": dict(params),
            "timeoutMs": round(self.request_timeout_seconds * 1000),
        }
        if version is not None:
            request["version"] = version
        if source_client_id is not None:
            request["sourceClientId"] = source_client_id
        if target_client_id is not None:
            request["targetClientId"] = target_client_id
        dispatched = False
        try:
            writer.write(_encode_frame(request))
            await writer.drain()
            dispatched = True
            async with asyncio.timeout(self.request_timeout_seconds):
                while True:
                    response = await _read_frame(reader)
                    if response.get("type") == "client-discovery-request":
                        await _decline_discovery(writer, response)
                        continue
                    if (
                        response.get("type") == "response"
                        and response.get("requestId") == request_id
                    ):
                        return response
        except (OSError, TimeoutError, asyncio.IncompleteReadError, _IpcProtocolError) as exc:
            if dispatched and dispatched_is_ambiguous:
                raise _DispatchedRequestError(
                    f"Codex steering outcome is unknown after dispatch: {exc}"
                ) from exc
            raise


class _IpcProtocolError(RuntimeError):
    pass


class _DispatchedRequestError(RuntimeError):
    pass


def _encode_frame(message: Mapping[str, Any]) -> bytes:
    payload = json.dumps(message, separators=(",", ":")).encode()
    if not payload or len(payload) > _MAX_FRAME_BYTES:
        raise _IpcProtocolError("Codex IPC frame is outside the supported size")
    return _FRAME_HEADER.pack(len(payload)) + payload


async def _read_frame(reader: asyncio.StreamReader) -> Mapping[str, Any]:
    header = await reader.readexactly(_FRAME_HEADER.size)
    size = _FRAME_HEADER.unpack(header)[0]
    if size == 0 or size > _MAX_FRAME_BYTES:
        raise _IpcProtocolError(f"invalid Codex IPC frame length: {size}")
    payload = await reader.readexactly(size)
    try:
        message = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _IpcProtocolError("Codex IPC returned malformed JSON") from exc
    if not isinstance(message, Mapping):
        raise _IpcProtocolError("Codex IPC returned a non-object message")
    return message


async def _decline_discovery(
    writer: asyncio.StreamWriter, request: Mapping[str, Any]
) -> None:
    request_id = request.get("requestId")
    if not isinstance(request_id, str):
        return
    writer.write(
        _encode_frame(
            {
                "type": "client-discovery-response",
                "requestId": request_id,
                "response": {"canHandle": False},
            }
        )
    )
    await writer.drain()


def _steer_params(
    *, provider_thread_id: str, cwd: str, prompt: str, message_id: str
) -> dict[str, Any]:
    context = {
        "prompt": prompt,
        "workspaceRoots": [cwd],
        "collaborationMode": None,
        "imageAttachments": [],
        "fileAttachments": [],
        "pastedTextAttachments": [],
        "addedFiles": [],
        "commentAttachments": [],
        "mcpAppModelContextAttachments": [],
        "appshotContexts": [],
    }
    return {
        "conversationId": provider_thread_id,
        "clientUserMessageId": message_id,
        "input": [{"type": "text", "text": prompt, "text_elements": []}],
        "serviceTier": None,
        "attachments": [],
        "additionalContext": None,
        "restoreMessage": {
            "id": message_id,
            "text": prompt,
            "context": context,
            "cwd": cwd,
            "createdAt": round(time.time() * 1000),
        },
    }


def _nested_text(value: Mapping[str, Any], *path: str) -> str | None:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current if isinstance(current, str) and current else None
