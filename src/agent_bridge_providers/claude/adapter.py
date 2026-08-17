"""Read Claude Code's local, durable conversation records without resuming them."""

from __future__ import annotations

import json
import shlex
from collections.abc import AsyncIterator, Iterable, Mapping
from pathlib import Path
from typing import Any

from agent_bridge_providers.codex.adapter import DiscoveredConversation


class ClaudeCatalogAdapter:
    """Discover Claude Code JSONL sessions from a configurable local root.

    Claude Code does not expose a Catalog API comparable to Codex App Server.
    This adapter therefore treats the on-disk session log as a read-only provider
    interface. Malformed records are skipped so a partially written active file
    cannot break reconciliation.
    """

    def __init__(self, claude_home: Path | None = None, *, claude_bin: str = "claude") -> None:
        self._home = claude_home or Path.home() / ".claude"
        self._claude_bin = claude_bin

    async def close(self) -> None:
        return None

    async def discover(
        self, *, include_turns: bool = True
    ) -> AsyncIterator[DiscoveredConversation]:
        history = _read_history(self._home / "history.jsonl")
        projects = self._home / "projects"
        if not projects.is_dir():
            return
        for path in sorted(projects.glob("*/*.jsonl")):
            record = _read_session(
                path,
                history,
                include_turns=include_turns,
                claude_bin=self._claude_bin,
            )
            if record is not None:
                yield record
            subagents = path.with_suffix("") / "subagents"
            if subagents.is_dir():
                for child_path in sorted(subagents.glob("*.jsonl")):
                    child = _read_session(
                        child_path,
                        history,
                        include_turns=include_turns,
                        parent_thread_id=path.stem,
                        claude_bin=self._claude_bin,
                    )
                    if child is not None:
                        yield child


def _read_history(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in _json_lines(path):
        session_id = item.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            continue
        current = result.setdefault(session_id, {})
        display = item.get("display")
        if isinstance(display, str) and display.strip() and not display.startswith("/"):
            current.setdefault("display", display.strip())
        project = item.get("project")
        if isinstance(project, str) and project:
            current["project"] = project
        timestamp = item.get("timestamp")
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
            current["timestamp"] = max(timestamp, current.get("timestamp", timestamp))
    return result


def _read_session(
    path: Path,
    history: Mapping[str, Mapping[str, Any]],
    *,
    include_turns: bool,
    parent_thread_id: str | None = None,
    claude_bin: str = "claude",
) -> DiscoveredConversation | None:
    records = list(_json_lines(path))
    messages = [item for item in records if item.get("type") in {"user", "assistant"}]
    seed = next((item for item in messages if isinstance(item.get("sessionId"), str)), None)
    if seed is None:
        return None
    session_id = str(seed["sessionId"])
    agent_id = next(
        (str(item["agentId"]) for item in messages if isinstance(item.get("agentId"), str)),
        None,
    )
    provider_thread_id = f"{session_id}:agent:{agent_id}" if agent_id else session_id
    metadata = history.get(session_id, {})
    cwd = _last_string(messages, "cwd") or _string(metadata.get("project"))
    user_texts = list(_prose(messages, roles={"user"}))
    transcript = "\n\n".join(_prose(messages, roles={"user", "assistant"})) if include_turns else ""
    preview = next((text for text in user_texts if text), None)
    # history.jsonl describes the root session. A native subagent shares that session id,
    # but its useful label is its own first user prompt rather than the root's display text.
    history_title = None if agent_id else _string(metadata.get("display"))
    title = _title(history_title) or _title(preview)
    timestamps = [item["timestamp"] for item in messages if isinstance(item.get("timestamp"), str)]
    branch = _last_string(messages, "gitBranch")
    source_kind = "subAgent" if agent_id else "cli"
    raw: dict[str, Any] = {}
    version = _last_string(messages, "version")
    if version:
        raw["claude_code_version"] = version
    if agent_id:
        raw["agent_id"] = agent_id
    resume_id = session_id
    command = f"{shlex.quote(claude_bin)} --resume {shlex.quote(resume_id)}"
    resume_command = f"cd {shlex.quote(cwd)} && {command}" if cwd else command
    return DiscoveredConversation(
        provider="claude",
        provider_thread_id=provider_thread_id,
        title=title,
        preview=preview,
        cwd=cwd,
        source_kind=source_kind,
        model_provider="anthropic",
        created_at=timestamps[0] if timestamps else None,
        updated_at=timestamps[-1] if timestamps else _history_timestamp(metadata),
        status="inactive",
        parent_thread_id=parent_thread_id,
        git_branch=branch,
        transcript_text=transcript,
        resume_command=resume_command,
        raw_metadata=raw,
    )


def _json_lines(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def _prose(records: Iterable[Mapping[str, Any]], *, roles: set[str]) -> Iterable[str]:
    for record in records:
        message = record.get("message")
        if not isinstance(message, Mapping) or message.get("role") not in roles:
            continue
        content = message.get("content")
        if isinstance(content, str):
            cleaned = content.strip()
            if cleaned:
                yield cleaned
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, Mapping) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        yield text.strip()


def _last_string(records: Iterable[Mapping[str, Any]], key: str) -> str | None:
    values = [value for item in records if (value := _string(item.get(key))) is not None]
    return values[-1] if values else None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _title(preview: str | None) -> str | None:
    if preview is None:
        return None
    first_line = next((line.strip() for line in preview.splitlines() if line.strip()), "")
    normalized = " ".join(first_line.split())
    if not normalized:
        return None
    return normalized if len(normalized) <= 96 else f"{normalized[:95].rstrip()}…"


def _history_timestamp(metadata: Mapping[str, Any]) -> int | float | None:
    value = metadata.get("timestamp")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value / 1000
    return None
