"""Provider-neutral discovery adapter backed by Codex App Server."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .app_server import AppServerClient, AppServerError, AppServerProtocolError

# Omitting sourceKinds makes App Server return only cli/vscode threads.  The
# catalog also needs native subagent ancestry, so discovery explicitly requests
# every currently documented stable source value by default.
ALL_SOURCE_KINDS = (
    "cli",
    "vscode",
    "exec",
    "appServer",
    "subAgent",
    "subAgentReview",
    "subAgentCompact",
    "subAgentThreadSpawn",
    "subAgentOther",
    "unknown",
)


@dataclass(frozen=True, slots=True)
class DiscoveredConversation:
    """Catalog input that does not expose Codex-specific wire shapes."""

    provider: str
    provider_thread_id: str
    title: str | None
    preview: str | None
    cwd: str | None
    source_kind: str | None
    model_provider: str | None
    created_at: int | float | str | None
    updated_at: int | float | str | None
    status: str
    active_flags: tuple[str, ...] = ()
    parent_thread_id: str | None = None
    git_sha: str | None = None
    git_branch: str | None = None
    git_origin_url: str | None = None
    is_pinned: bool = False
    is_ephemeral: bool = False
    is_archived: bool = False
    transcript_text: str = ""
    resume_command: str | None = None
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)


class CodexCatalogAdapter:
    """Discover Codex conversations without loading or resuming them."""

    def __init__(
        self,
        client: AppServerClient | None = None,
        *,
        codex_bin: str = "codex",
        codex_home: Path | None = None,
    ) -> None:
        self._client = client or AppServerClient((codex_bin, "app-server"))
        self._owns_client = client is None
        self._codex_home = codex_home

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def discover(
        self, *, include_turns: bool = True
    ) -> AsyncIterator[DiscoveredConversation]:
        """Yield active and archived conversations for Catalog reconciliation.

        Listing stays cheap when transcript indexing is disabled. With turns
        enabled, each summary is read without resuming or subscribing to the
        thread, and only user/agent prose is extracted.
        """

        discovered = 0
        for archived in (False, True):
            async for summary in self.iter_conversations(archived=archived):
                discovered += 1
                if include_turns:
                    try:
                        yield await self.get_conversation(
                            summary.provider_thread_id,
                            include_turns=True,
                            archived=archived,
                        )
                    except AppServerError:
                        # One thread can become unreadable when its stored item
                        # schema is newer than the installed App Server. Keep
                        # catalog reconciliation alive with the list summary;
                        # a future pass can index its transcript after Codex is
                        # upgraded or the stored item is repaired.
                        yield summary
                else:
                    yield summary
        if discovered == 0:
            from .local_sessions import LocalCodexSessionReader

            fallback = LocalCodexSessionReader(self._codex_home)
            async for record in fallback.discover(include_turns=include_turns):
                yield record

    async def iter_conversations(
        self,
        *,
        page_size: int = 100,
        source_kinds: Sequence[str] | None = None,
        archived: bool = False,
        cwd: str | Sequence[str] | None = None,
        search_term: str | None = None,
        use_state_db_only: bool = False,
    ) -> AsyncIterator[DiscoveredConversation]:
        if page_size < 1:
            raise ValueError("page_size must be positive")
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params: dict[str, Any] = {
                "limit": page_size,
                "sortKey": "updated_at",
                "sortDirection": "desc",
                "archived": archived,
                "useStateDbOnly": use_state_db_only,
            }
            if cursor is not None:
                params["cursor"] = cursor
            params["sourceKinds"] = list(ALL_SOURCE_KINDS if source_kinds is None else source_kinds)
            if cwd is not None:
                params["cwd"] = list(cwd) if not isinstance(cwd, str) else cwd
            if search_term is not None:
                params["searchTerm"] = search_term
            page = await self._client.list_threads_page(**params)
            data = page.get("data")
            if not isinstance(data, list):
                raise AppServerProtocolError("thread/list result.data must be an array")
            for thread in data:
                if not isinstance(thread, Mapping):
                    raise AppServerProtocolError("thread/list data item must be an object")
                yield self.map_thread(thread, archived=archived)
            next_cursor = page.get("nextCursor")
            if next_cursor is None:
                return
            if not isinstance(next_cursor, str):
                raise AppServerProtocolError("thread/list nextCursor must be a string or null")
            if next_cursor in seen_cursors:
                raise AppServerProtocolError("thread/list returned a repeated cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    async def list_conversations(self, **filters: Any) -> list[DiscoveredConversation]:
        return [item async for item in self.iter_conversations(**filters)]

    async def get_conversation(
        self,
        thread_id: str,
        *,
        include_turns: bool = False,
        archived: bool = False,
    ) -> DiscoveredConversation:
        return self.map_thread(
            await self._client.read_thread(thread_id, include_turns=include_turns),
            archived=archived,
        )

    @staticmethod
    def map_thread(thread: Mapping[str, Any], *, archived: bool = False) -> DiscoveredConversation:
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise AppServerProtocolError("thread.id must be a non-empty string")
        status_value = thread.get("status")
        status = "unknown"
        active_flags: tuple[str, ...] = ()
        if isinstance(status_value, Mapping):
            if isinstance(status_value.get("type"), str):
                status = status_value["type"]
            flags = status_value.get("activeFlags")
            if isinstance(flags, list):
                active_flags = tuple(str(flag) for flag in flags)
        git = thread.get("gitInfo")
        git = git if isinstance(git, Mapping) else {}

        known = {
            "id",
            "name",
            "preview",
            "cwd",
            "source",
            "sourceKind",
            "modelProvider",
            "createdAt",
            "updatedAt",
            "status",
            "parentThreadId",
            "gitInfo",
            "isPinned",
            "ephemeral",
            "turns",
        }
        return DiscoveredConversation(
            provider="codex",
            provider_thread_id=thread_id,
            title=_optional_string(thread.get("name")),
            preview=_optional_string(thread.get("preview")),
            cwd=_optional_string(thread.get("cwd")),
            source_kind=_source_kind(thread),
            model_provider=_optional_string(thread.get("modelProvider")),
            created_at=_optional_number(thread.get("createdAt")),
            updated_at=_optional_number(thread.get("updatedAt")),
            status=status,
            active_flags=active_flags,
            parent_thread_id=_optional_string(thread.get("parentThreadId")),
            git_sha=_optional_string(git.get("sha")),
            git_branch=_optional_string(git.get("branch")),
            git_origin_url=_optional_string(git.get("originUrl")),
            is_pinned=thread.get("isPinned") is True,
            is_ephemeral=thread.get("ephemeral") is True,
            is_archived=archived,
            transcript_text=_transcript_text(thread.get("turns")),
            raw_metadata={key: value for key, value in thread.items() if key not in known},
        )


def _source_kind(thread: Mapping[str, Any]) -> str | None:
    source = thread.get("sourceKind", thread.get("source"))
    if isinstance(source, str):
        return source
    if isinstance(source, Mapping):
        for key in ("type", "kind"):
            value = source.get(key)
            if isinstance(value, str):
                return value
    return None


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _optional_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _transcript_text(value: Any) -> str:
    """Extract only human/agent prose, excluding command and tool output."""

    if not isinstance(value, list):
        return ""
    lines: list[str] = []
    for turn in value:
        if not isinstance(turn, Mapping):
            continue
        items = turn.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            item_type = item.get("type")
            if item_type not in {"userMessage", "agentMessage", "user_message", "agent_message"}:
                continue
            role = "user" if str(item_type).startswith("user") else "assistant"
            texts = _message_texts(item)
            if texts:
                lines.append(f"{role}: {' '.join(texts)}")
    return "\n".join(lines)


def _message_texts(item: Mapping[str, Any]) -> list[str]:
    direct = item.get("text")
    if isinstance(direct, str) and direct.strip():
        return [direct.strip()]
    content = item.get("content")
    if not isinstance(content, list):
        return []
    result: list[str] = []
    for part in content:
        if isinstance(part, str) and part.strip():
            result.append(part.strip())
        elif isinstance(part, Mapping) and part.get("type") in {
            "text",
            "input_text",
            "output_text",
        }:
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                result.append(text.strip())
    return result
