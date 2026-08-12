"""Read-only compatibility discovery for persisted Codex session JSONL files."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from .adapter import DiscoveredConversation


class LocalCodexSessionReader:
    """Discover sessions when the installed App Server does not index them yet.

    Only session metadata plus explicit user/agent messages are retained. Tool
    calls, command output, reasoning, and configuration instructions are never
    copied into the discovered transcript.
    """

    def __init__(self, codex_home: Path | None = None) -> None:
        configured = os.environ.get("CODEX_HOME")
        self.codex_home = codex_home or (Path(configured) if configured else Path.home() / ".codex")

    def has_sessions(self) -> bool:
        return any(self._session_files())

    async def discover(
        self, *, include_turns: bool = True
    ) -> AsyncIterator[DiscoveredConversation]:
        names = self._thread_names()
        for path, archived in self._session_files():
            record = self._read_session(
                path,
                archived=archived,
                include_turns=include_turns,
                names=names,
            )
            if record is not None:
                yield record

    def _session_files(self) -> list[tuple[Path, bool]]:
        files: list[tuple[Path, bool]] = []
        for directory, archived in (
            (self.codex_home / "sessions", False),
            (self.codex_home / "archived_sessions", True),
        ):
            if directory.is_dir():
                files.extend((path, archived) for path in directory.rglob("*.jsonl"))
        return sorted(files, key=lambda entry: str(entry[0]))

    def _thread_names(self) -> dict[str, str]:
        result: dict[str, str] = {}
        path = self.codex_home / "session_index.jsonl"
        if not path.is_file():
            return result
        for raw_line in path.open(encoding="utf-8", errors="replace"):
            value = _json_object(raw_line)
            if (
                value
                and isinstance(value.get("id"), str)
                and isinstance(value.get("thread_name"), str)
            ):
                result[value["id"]] = value["thread_name"]
        return result

    @staticmethod
    def _read_session(
        path: Path,
        *,
        archived: bool,
        include_turns: bool,
        names: Mapping[str, str],
    ) -> DiscoveredConversation | None:
        metadata: dict[str, Any] | None = None
        messages: list[tuple[str, str]] = []
        last_timestamp: str | None = None
        for raw_line in path.open(encoding="utf-8", errors="replace"):
            record = _json_object(raw_line)
            if record is None:
                continue
            timestamp = record.get("timestamp")
            if isinstance(timestamp, str):
                last_timestamp = timestamp
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            if record.get("type") == "session_meta" and metadata is None:
                metadata = payload
            if include_turns and record.get("type") == "event_msg":
                message = _event_message(payload)
                if message is not None:
                    messages.append(message)
        if metadata is None or not isinstance(metadata.get("id"), str):
            return None
        thread_id = metadata["id"]
        git_value = metadata.get("git")
        git: Mapping[str, Any] = git_value if isinstance(git_value, dict) else {}
        first_user = next((text for role, text in messages if role == "user"), "")
        title = names.get(thread_id) or (first_user[:160] if first_user else None)
        source, parent = _source(metadata)
        return DiscoveredConversation(
            provider="codex",
            provider_thread_id=thread_id,
            title=title,
            preview=first_user[:500] or None,
            cwd=_string(metadata.get("cwd")),
            source_kind=source,
            model_provider=_string(metadata.get("model_provider")),
            created_at=_string(metadata.get("timestamp")),
            updated_at=last_timestamp or _string(metadata.get("timestamp")),
            status="archived" if archived else "notLoaded",
            parent_thread_id=parent,
            git_sha=_string(git.get("commit_hash", git.get("sha"))),
            git_branch=_string(git.get("branch")),
            git_origin_url=_string(git.get("repository_url", git.get("originUrl"))),
            is_archived=archived,
            transcript_text="\n".join(f"{role}: {text}" for role, text in messages),
            raw_metadata={
                "originator": metadata.get("originator"),
                "cli_version": metadata.get("cli_version"),
                "fallback": "local_session_jsonl",
            },
        )


def _json_object(raw_line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw_line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _event_message(payload: Mapping[str, Any]) -> tuple[str, str] | None:
    event_type = payload.get("type")
    if event_type == "user_message":
        role = "user"
    elif event_type == "agent_message":
        role = "assistant"
    else:
        return None
    value = payload.get("message", payload.get("content"))
    if not isinstance(value, str) or not value.strip():
        return None
    return role, value.strip()


def _source(metadata: Mapping[str, Any]) -> tuple[str | None, str | None]:
    parent = _string(metadata.get("parent_thread_id"))
    value = metadata.get("source")
    if isinstance(value, str):
        return value, parent
    if isinstance(value, Mapping):
        if "subagent" in value or "subAgent" in value:
            nested = value.get("subagent", value.get("subAgent"))
            if isinstance(nested, Mapping):
                parent = parent or _string(
                    nested.get("parent_thread_id", nested.get("parentThreadId"))
                )
            return "subAgent", parent
        kind = value.get("type", value.get("kind"))
        return (_string(kind), parent)
    return None, parent


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None
