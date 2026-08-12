"""Offline-safe backup, retention, deletion, and redaction utilities."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, text

from .config import Settings
from .db import (
    BrokerDeadLetterRow,
    BrokerDeliveryRow,
    BrokerMessageRow,
    CollaborationMessageRow,
    CoordinatorActivationRow,
    Database,
)


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    broker_days: int = 30
    resolved_dead_letter_days: int = 30
    collaboration_days: int = 180
    coordinator_activation_days: int = 180

    def __post_init__(self) -> None:
        for name, value in (
            ("broker_days", self.broker_days),
            ("resolved_dead_letter_days", self.resolved_dead_letter_days),
            ("collaboration_days", self.collaboration_days),
            ("coordinator_activation_days", self.coordinator_activation_days),
        ):
            if value < 1:
                raise ValueError(f"{name} must be at least one day")


class MaintenanceService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def apply_retention(
        self, policy: RetentionPolicy, *, now: datetime | None = None
    ) -> dict[str, int]:
        current = now or datetime.now(UTC)
        counts: dict[str, int] = {}
        with self.database.session() as session:
            result = session.execute(
                delete(BrokerDeliveryRow).where(
                    BrokerDeliveryRow.last_observed_at
                    < current - timedelta(days=policy.broker_days),
                    BrokerDeliveryRow.state.in_(("acknowledged", "dead_lettered", "expired")),
                )
            )
            counts["broker_deliveries"] = _affected(result)
            result = session.execute(
                delete(BrokerDeadLetterRow).where(
                    BrokerDeadLetterRow.resolved_at.is_not(None),
                    BrokerDeadLetterRow.resolved_at
                    < current - timedelta(days=policy.resolved_dead_letter_days),
                )
            )
            counts["resolved_dead_letters"] = _affected(result)
            result = session.execute(
                delete(BrokerMessageRow).where(
                    BrokerMessageRow.last_observed_at
                    < current - timedelta(days=policy.broker_days),
                    ~BrokerMessageRow.message_id.in_(
                        session.query(BrokerDeliveryRow.message_id).distinct()
                    ),
                    ~BrokerMessageRow.message_id.in_(
                        session.query(BrokerDeadLetterRow.message_id).distinct()
                    ),
                )
            )
            counts["broker_messages"] = _affected(result)
            result = session.execute(
                delete(CollaborationMessageRow).where(
                    CollaborationMessageRow.updated_at
                    < current - timedelta(days=policy.collaboration_days),
                    CollaborationMessageRow.state.in_(("published", "received", "failed")),
                )
            )
            counts["collaboration_messages"] = _affected(result)
            result = session.execute(
                delete(CoordinatorActivationRow).where(
                    CoordinatorActivationRow.updated_at
                    < current - timedelta(days=policy.coordinator_activation_days),
                    CoordinatorActivationRow.status.in_(("completed", "failed", "expired")),
                )
            )
            counts["coordinator_activations"] = _affected(result)
            session.commit()
        return counts

    def delete_transcript(self, conversation_id: str) -> bool:
        """Erase transcript and raw provider payload, then rebuild the FTS row."""

        from .db import ConversationRow

        with self.database.session() as session:
            row = session.get(ConversationRow, conversation_id)
            if row is None:
                return False
            row.transcript_text = ""
            row.preview = ""
            raw = json.loads(row.raw_metadata_json)
            if isinstance(raw, dict):
                raw = _strip_transcript_payload(raw)
                raw["agent_bridge_transcript_deleted_at"] = datetime.now(UTC).isoformat()
            row.raw_metadata_json = json.dumps(raw, default=str, separators=(",", ":"))
            session.execute(
                text("DELETE FROM conversation_fts WHERE conversation_id = :conversation_id"),
                {"conversation_id": conversation_id},
            )
            session.execute(
                text(
                    """INSERT INTO conversation_fts
                    (conversation_id, title, preview, transcript_text, notes, tags)
                    VALUES (:conversation_id, :title, '', '', :notes, :tags)"""
                ),
                {
                    "conversation_id": conversation_id,
                    "title": row.title,
                    "notes": row.notes,
                    "tags": " ".join(row.tags),
                },
            )
            session.commit()
            return True


def sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix) or database_url == "sqlite:///:memory:":
        raise ValueError("maintenance requires a file-backed sqlite:/// database URL")
    path = Path(database_url.removeprefix(prefix)).expanduser().resolve()
    if not path.is_absolute():
        raise ValueError("SQLite database path must be absolute")
    return path


def backup_database(database_url: str, destination: Path) -> dict[str, Any]:
    source = sqlite_path(database_url)
    target = destination.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source database does not exist: {source}")
    if target.exists():
        raise FileExistsError(f"backup destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_connection, sqlite3.connect(target) as target_connection:
        source_connection.backup(target_connection)
    try:
        verification = verify_database(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {"source": str(source), "backup": str(target), **verification}


def verify_database(path: Path) -> dict[str, Any]:
    candidate = path.expanduser().resolve()
    if not candidate.is_file():
        raise FileNotFoundError(f"database does not exist: {candidate}")
    with sqlite3.connect(f"file:{candidate}?mode=ro", uri=True) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise ValueError(f"SQLite integrity check failed: {integrity}")
        tables = int(
            connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchone()[0]
        )
        revision_row = connection.execute(
            "SELECT version_num FROM alembic_version LIMIT 1"
        ).fetchone()
    if revision_row is None:
        raise ValueError("database has no Alembic revision")
    return {"integrity": integrity, "tables": tables, "revision": str(revision_row[0])}


def restore_database(source: Path, destination: Path) -> dict[str, Any]:
    backup = source.expanduser().resolve()
    target = destination.expanduser().resolve()
    verification = verify_database(backup)
    if target.exists():
        raise FileExistsError("restore destination exists; restore never overwrites a database")
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(backup) as source_connection, sqlite3.connect(target) as target_connection:
        source_connection.backup(target_connection)
    restored = verify_database(target)
    if restored["revision"] != verification["revision"]:
        target.unlink(missing_ok=True)
        raise ValueError("restored database revision differs from backup")
    return {"source": str(backup), "restored": str(target), **restored}


_SECRET_KEYS = {
    "authorization",
    "credential",
    "credential_hash",
    "credential_salt",
    "nats_password",
    "password",
    "secret",
    "token",
}


def redact_sensitive(value: Any) -> Any:
    """Recursively redact conventional secret fields before structured logging."""

    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if str(key).casefold() in _SECRET_KEYS
            else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value


def _strip_transcript_payload(value: dict[str, Any]) -> dict[str, Any]:
    transcript_keys = {"turns", "messages", "transcript", "transcript_text"}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key.casefold() in transcript_keys:
            continue
        if isinstance(item, dict):
            result[key] = _strip_transcript_payload(item)
        elif isinstance(item, list):
            result[key] = [
                _strip_transcript_payload(entry) if isinstance(entry, dict) else entry
                for entry in item
            ]
        else:
            result[key] = item
    return result


def _affected(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-bridge-maintenance")
    parser.add_argument("--database-url", default=os.environ.get("AGENT_BRIDGE_DATABASE_URL"))
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("database", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("source", type=Path)
    restore.add_argument("destination", type=Path)
    retention = commands.add_parser("retention")
    retention.add_argument("--broker-days", type=int, default=30)
    retention.add_argument("--collaboration-days", type=int, default=180)
    transcript = commands.add_parser("delete-transcript")
    transcript.add_argument("conversation_id")
    args = parser.parse_args()
    database_url = args.database_url or Settings.from_environment().database_url
    if args.command == "backup":
        result = backup_database(database_url, args.destination)
    elif args.command == "verify":
        result = verify_database(args.database)
    elif args.command == "restore":
        result = restore_database(args.source, args.destination)
    else:
        database = Database(database_url)
        database.initialize()
        service = MaintenanceService(database)
        if args.command == "retention":
            result = service.apply_retention(
                RetentionPolicy(
                    broker_days=args.broker_days,
                    collaboration_days=args.collaboration_days,
                )
            )
        else:
            result = {"deleted": service.delete_transcript(args.conversation_id)}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
