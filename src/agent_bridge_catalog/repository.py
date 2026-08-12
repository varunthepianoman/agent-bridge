from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from agent_bridge_protocol import conversation_id as protocol_conversation_id

from .db import ConversationRow, Database


def stable_conversation_id(
    provider: str, provider_thread_id: str, node_id: str, environment_id: str
) -> str:
    return protocol_conversation_id(
        provider=provider,
        provider_thread_id=provider_thread_id,
        node_id=node_id,
        environment_id=environment_id,
    )


class CatalogRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def upsert_discovered(self, item: Any, *, node_id: str, environment_id: str) -> ConversationRow:
        if isinstance(item, dict):
            payload = item
        elif is_dataclass(item) and not isinstance(item, type):
            payload = asdict(item)
        else:
            payload = vars(item)
        provider = str(payload.get("provider", "codex"))
        thread_id = str(payload["provider_thread_id"])
        conversation_id = stable_conversation_id(provider, thread_id, node_id, environment_id)
        now = datetime.now(UTC)
        with self.database.session() as session:
            row = session.get(ConversationRow, conversation_id)
            is_new = row is None
            if row is None:
                row = ConversationRow(
                    conversation_id=conversation_id,
                    provider=provider,
                    provider_thread_id=thread_id,
                    node_id=node_id,
                    environment_id=environment_id,
                    last_synced_at=now,
                )
                session.add(row)
            old_provider_title = row.provider_title
            provider_title = _optional(payload.get("title"))
            row.provider_title = provider_title
            if is_new or row.title == old_provider_title or not row.title:
                row.title = (
                    provider_title or _optional(payload.get("preview")) or "Untitled conversation"
                )
            row.preview = str(payload.get("preview") or "")
            row.transcript_text = str(payload.get("transcript_text") or "")
            row.status = str(payload.get("status") or "idle")
            row.source = _optional(payload.get("source", payload.get("source_kind")))
            row.cwd = _optional(payload.get("cwd"))
            row.repository = _optional(payload.get("repository", payload.get("git_origin_url")))
            row.branch = _optional(payload.get("branch", payload.get("git_branch")))
            row.commit_hash = _optional(payload.get("commit_hash", payload.get("git_sha")))
            row.parent_provider_thread_id = _optional(
                payload.get("parent_provider_thread_id", payload.get("parent_thread_id"))
            )
            row.created_at = _datetime(payload.get("created_at"))
            row.last_activity_at = _datetime(
                payload.get("last_activity_at", payload.get("updated_at"))
            )
            row.archived = bool(payload.get("archived", payload.get("is_archived", row.archived)))
            row.resume_command = _optional(payload.get("resume_command")) or _resume_command(
                provider, thread_id, row.cwd
            )
            row.last_synced_at = now
            raw = payload.get("raw_metadata", payload)
            row.raw_metadata_json = json.dumps(raw, default=str, separators=(",", ":"))
            session.flush()
            self._refresh_fts(session, row)
            session.commit()
            return row

    def resolve_parents(self) -> None:
        with self.database.session() as session:
            rows = session.scalars(select(ConversationRow)).all()
            by_thread = {
                (row.provider, row.provider_thread_id, row.node_id, row.environment_id): row
                for row in rows
            }
            for row in rows:
                if row.parent_provider_thread_id:
                    parent = by_thread.get(
                        (
                            row.provider,
                            row.parent_provider_thread_id,
                            row.node_id,
                            row.environment_id,
                        )
                    )
                    row.parent_conversation_id = parent.conversation_id if parent else None
            session.commit()

    def list(
        self,
        *,
        query: str | None = None,
        provider: str | None = None,
        source: str | None = None,
        status: str | None = None,
        archived: bool | None = None,
        pinned: bool | None = None,
        include_hidden: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[ConversationRow], int]:
        with self.database.session() as session:
            statement = select(ConversationRow)
            count_statement = select(func.count()).select_from(ConversationRow)
            if query:
                ids = (
                    select(text("conversation_id"))
                    .select_from(text("conversation_fts"))
                    .where(text("conversation_fts MATCH :query"))
                    .params(query=_fts_query(query))
                )
                statement = statement.where(ConversationRow.conversation_id.in_(ids))
                count_statement = count_statement.where(ConversationRow.conversation_id.in_(ids))
            if provider:
                statement = statement.where(ConversationRow.provider == provider)
                count_statement = count_statement.where(ConversationRow.provider == provider)
            if source:
                statement = statement.where(ConversationRow.source == source)
                count_statement = count_statement.where(ConversationRow.source == source)
            if status:
                statement = statement.where(ConversationRow.status == status)
                count_statement = count_statement.where(ConversationRow.status == status)
            if archived is not None:
                statement = statement.where(ConversationRow.archived == archived)
                count_statement = count_statement.where(ConversationRow.archived == archived)
            if pinned is not None:
                statement = statement.where(ConversationRow.pinned == pinned)
                count_statement = count_statement.where(ConversationRow.pinned == pinned)
            if not include_hidden:
                statement = statement.where(ConversationRow.hidden.is_(False))
                count_statement = count_statement.where(ConversationRow.hidden.is_(False))
            statement = (
                statement.order_by(
                    ConversationRow.pinned.desc(), ConversationRow.last_activity_at.desc()
                )
                .limit(limit)
                .offset(offset)
            )
            return list(session.scalars(statement)), int(session.scalar(count_statement) or 0)

    def get(self, conversation_id: str) -> ConversationRow | None:
        with self.database.session() as session:
            return session.get(ConversationRow, conversation_id)

    def update_metadata(
        self, conversation_id: str, changes: dict[str, Any]
    ) -> ConversationRow | None:
        allowed = {"title", "notes", "pinned", "hidden", "archived", "tags"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported metadata fields: {', '.join(sorted(unknown))}")
        with self.database.session() as session:
            row = session.get(ConversationRow, conversation_id)
            if row is None:
                return None
            for key, value in changes.items():
                if key == "tags":
                    normalized = sorted({str(tag).strip() for tag in value if str(tag).strip()})
                    row.tags_json = json.dumps(normalized)
                else:
                    setattr(row, key, value)
            session.flush()
            self._refresh_fts(session, row)
            session.commit()
            return row

    def _refresh_fts(self, session: Session, row: ConversationRow) -> None:
        session.execute(
            text("DELETE FROM conversation_fts WHERE conversation_id = :conversation_id"),
            {"conversation_id": row.conversation_id},
        )
        session.execute(
            text(
                """INSERT INTO conversation_fts
                (conversation_id, title, preview, transcript_text, notes, tags)
                VALUES (:conversation_id, :title, :preview, :transcript_text, :notes, :tags)"""
            ),
            {
                "conversation_id": row.conversation_id,
                "title": row.title,
                "preview": row.preview,
                "transcript_text": row.transcript_text,
                "notes": row.notes,
                "tags": " ".join(row.tags),
            },
        )


def _resume_command(provider: str, thread_id: str, cwd: str | None) -> str:
    import shlex

    if provider.casefold() == "claude":
        # Claude resumes in the process cwd and has no equivalent of Codex -C.
        resume_id = thread_id.split(":agent:", 1)[0]
        command = f"claude --resume {shlex.quote(resume_id)}"
        return f"cd {shlex.quote(cwd)} && {command}" if cwd else command
    command = f"codex resume {shlex.quote(thread_id)}"
    return f"{command} -C {shlex.quote(cwd)}" if cwd else command


def _optional(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _fts_query(query: str) -> str:
    terms: Iterable[str] = (term.strip('"*') for term in query.split())
    cleaned = [term for term in terms if term]
    return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"*' for term in cleaned) or '""'
