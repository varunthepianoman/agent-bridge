"""Durable node-local execution lifecycle and idempotency storage."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from agent_bridge_protocol.models import (
    ExecutionAttempt,
    ExecutionFailure,
    ExecutionLease,
    ExecutionProgress,
    ExecutionResult,
    ExecutionStatus,
)

from .idempotency import ClaimResult


class LeaseBusyError(RuntimeError):
    pass


class StaleLeaseError(RuntimeError):
    pass


Outcome = ExecutionResult | ExecutionFailure


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class SQLiteExecutionStore:
    """SQLite store safe across restarts and coordinated by immediate transactions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self._initialize()

    def close(self) -> None:
        self._connection.close()

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS idempotency_claims (
                key TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('claimed', 'completed')),
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS execution_attempts (
                attempt_id TEXT PRIMARY KEY,
                execution_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                node_id TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error TEXT,
                UNIQUE (execution_id, attempt_number)
            );
            CREATE TABLE IF NOT EXISTS execution_progress (
                execution_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (attempt_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS execution_outcomes (
                execution_id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('result', 'failure')),
                payload_json TEXT NOT NULL,
                result_published INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS execution_leases (
                execution_id TEXT PRIMARY KEY,
                holder_id TEXT NOT NULL,
                fencing_token INTEGER NOT NULL,
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS execution_cancellations (
                execution_id TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                requested_at TEXT NOT NULL
            );
            """
        )

    async def claim(self, key: str, *, owner: str, ttl_seconds: float) -> ClaimResult:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        async with self._lock:
            now = _now()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT owner, status, expires_at FROM idempotency_claims WHERE key = ?",
                    (key,),
                ).fetchone()
                if row is not None and row["status"] == "completed":
                    self._connection.commit()
                    return ClaimResult.COMPLETED
                if (
                    row is not None
                    and _parse_time(row["expires_at"]) > now
                    and row["owner"] != owner
                ):
                    self._connection.commit()
                    return ClaimResult.IN_PROGRESS
                expires_at = now + timedelta(seconds=ttl_seconds)
                self._connection.execute(
                    """
                    INSERT INTO idempotency_claims(key, owner, status, expires_at, updated_at)
                    VALUES (?, ?, 'claimed', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        owner = excluded.owner, status = 'claimed',
                        expires_at = excluded.expires_at, updated_at = excluded.updated_at
                    """,
                    (key, owner, expires_at.isoformat(), now.isoformat()),
                )
                self._connection.commit()
                return ClaimResult.CLAIMED
            except BaseException:
                self._connection.rollback()
                raise

    async def complete(self, key: str, *, owner: str) -> None:
        async with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_claim(key, owner)
                self._connection.execute(
                    "UPDATE idempotency_claims SET status = 'completed', "
                    "updated_at = ? WHERE key = ?",
                    (_now().isoformat(), key),
                )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    async def release(self, key: str, *, owner: str) -> None:
        async with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_claim(key, owner)
                self._connection.execute("DELETE FROM idempotency_claims WHERE key = ?", (key,))
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    def _require_claim(self, key: str, owner: str) -> None:
        row = self._connection.execute(
            "SELECT owner, status FROM idempotency_claims WHERE key = ?", (key,)
        ).fetchone()
        if row is None or row["owner"] != owner or row["status"] != "claimed":
            raise PermissionError(f"active idempotency claim {key!r} is not owned by {owner!r}")

    async def start_attempt(self, execution_id: str, *, node_id: str) -> ExecutionAttempt:
        async with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) AS number FROM execution_attempts "
                "WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            number = int(row["number"]) + 1
            started_at = _now()
            attempt = ExecutionAttempt(
                attempt_id=f"{execution_id}-attempt-{number}",
                execution_id=execution_id,
                attempt_number=number,
                node_id=node_id,
                status=ExecutionStatus.RUNNING,
                started_at=started_at,
            )
            self._connection.execute(
                """
                INSERT INTO execution_attempts(
                    attempt_id, execution_id, attempt_number, node_id, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    execution_id,
                    number,
                    node_id,
                    str(attempt.status),
                    started_at.isoformat(),
                ),
            )
            return attempt

    async def append_progress(
        self,
        attempt: ExecutionAttempt,
        *,
        summary: str,
        percent: float | None = None,
    ) -> ExecutionProgress:
        async with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), -1) AS sequence FROM execution_progress "
                "WHERE attempt_id = ?",
                (attempt.attempt_id,),
            ).fetchone()
            progress = ExecutionProgress(
                execution_id=attempt.execution_id,
                attempt_id=attempt.attempt_id,
                sequence=int(row["sequence"]) + 1,
                summary=summary,
                percent=percent,
            )
            self._connection.execute(
                "INSERT INTO execution_progress VALUES (?, ?, ?, ?)",
                (
                    progress.execution_id,
                    progress.attempt_id,
                    progress.sequence,
                    progress.model_dump_json(),
                ),
            )
            return progress

    async def finish(
        self,
        outcome: Outcome,
        *,
        message_id: str,
        claim_owner: str,
    ) -> None:
        """Atomically persist outcome and complete the input idempotency claim."""

        kind: Literal["result", "failure"] = (
            "result" if isinstance(outcome, ExecutionResult) else "failure"
        )
        async with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_claim(message_id, claim_owner)
                self._connection.execute(
                    """
                    INSERT INTO execution_outcomes(execution_id, attempt_id, kind, payload_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(execution_id) DO UPDATE SET
                        attempt_id = excluded.attempt_id,
                        kind = excluded.kind,
                        payload_json = excluded.payload_json
                    """,
                    (
                        outcome.execution_id,
                        outcome.attempt_id,
                        kind,
                        outcome.model_dump_json(),
                    ),
                )
                self._connection.execute(
                    """
                    UPDATE execution_attempts SET status = ?, finished_at = ?, error = ?
                    WHERE attempt_id = ?
                    """,
                    (
                        str(outcome.status),
                        _now().isoformat(),
                        outcome.message if isinstance(outcome, ExecutionFailure) else None,
                        outcome.attempt_id,
                    ),
                )
                self._connection.execute(
                    "UPDATE idempotency_claims SET status = 'completed', "
                    "updated_at = ? WHERE key = ?",
                    (_now().isoformat(), message_id),
                )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    async def record_retryable_failure(self, attempt: ExecutionAttempt, *, error: str) -> None:
        """Close one attempt while leaving the execution eligible for redelivery."""

        async with self._lock:
            self._connection.execute(
                """
                UPDATE execution_attempts SET status = ?, finished_at = ?, error = ?
                WHERE attempt_id = ?
                """,
                (ExecutionStatus.FAILED, _now().isoformat(), error, attempt.attempt_id),
            )

    async def outcome(self, execution_id: str) -> Outcome | None:
        async with self._lock:
            row = self._connection.execute(
                "SELECT kind, payload_json FROM execution_outcomes WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if row is None:
                return None
            model = ExecutionResult if row["kind"] == "result" else ExecutionFailure
            return model.model_validate_json(row["payload_json"])

    async def mark_result_published(self, execution_id: str) -> None:
        async with self._lock:
            self._connection.execute(
                "UPDATE execution_outcomes SET result_published = 1 WHERE execution_id = ?",
                (execution_id,),
            )

    async def acquire_lease(
        self, execution_id: str, *, holder_id: str, ttl_seconds: float
    ) -> ExecutionLease:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        async with self._lock:
            now = _now()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM execution_leases WHERE execution_id = ?", (execution_id,)
                ).fetchone()
                if row is not None and _parse_time(row["expires_at"]) > now:
                    raise LeaseBusyError(f"execution {execution_id!r} already has an active lease")
                token = 1 if row is None else int(row["fencing_token"]) + 1
                expires_at = now + timedelta(seconds=ttl_seconds)
                self._connection.execute(
                    """
                    INSERT INTO execution_leases VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(execution_id) DO UPDATE SET
                        holder_id=excluded.holder_id, fencing_token=excluded.fencing_token,
                        acquired_at=excluded.acquired_at, expires_at=excluded.expires_at
                    """,
                    (execution_id, holder_id, token, now.isoformat(), expires_at.isoformat()),
                )
                self._connection.commit()
                return ExecutionLease(
                    execution_id=execution_id,
                    holder_id=holder_id,
                    fencing_token=token,
                    acquired_at=now,
                    expires_at=expires_at,
                )
            except BaseException:
                self._connection.rollback()
                raise

    async def renew_lease(self, lease: ExecutionLease, *, ttl_seconds: float) -> ExecutionLease:
        async with self._lock:
            now = _now()
            row = self._connection.execute(
                "SELECT * FROM execution_leases WHERE execution_id = ?", (lease.execution_id,)
            ).fetchone()
            if (
                row is None
                or row["holder_id"] != lease.holder_id
                or row["fencing_token"] != lease.fencing_token
                or _parse_time(row["expires_at"]) <= now
            ):
                raise StaleLeaseError(f"stale lease for execution {lease.execution_id!r}")
            expires_at = now + timedelta(seconds=ttl_seconds)
            self._connection.execute(
                "UPDATE execution_leases SET expires_at = ? WHERE execution_id = ?",
                (expires_at.isoformat(), lease.execution_id),
            )
            return lease.model_copy(update={"expires_at": expires_at})

    async def release_lease(self, lease: ExecutionLease) -> None:
        async with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM execution_leases WHERE execution_id = ? AND holder_id = ? "
                "AND fencing_token = ?",
                (lease.execution_id, lease.holder_id, lease.fencing_token),
            )
            if cursor.rowcount != 1:
                raise StaleLeaseError(f"stale lease for execution {lease.execution_id!r}")

    async def request_cancellation(self, execution_id: str, *, reason: str) -> None:
        async with self._lock:
            self._connection.execute(
                """
                INSERT INTO execution_cancellations VALUES (?, ?, ?)
                ON CONFLICT(execution_id) DO UPDATE SET
                    reason=excluded.reason, requested_at=excluded.requested_at
                """,
                (execution_id, reason, _now().isoformat()),
            )

    async def cancellation_reason(self, execution_id: str) -> str | None:
        async with self._lock:
            row = self._connection.execute(
                "SELECT reason FROM execution_cancellations WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            return None if row is None else str(row["reason"])

    async def attempts(self, execution_id: str) -> list[ExecutionAttempt]:
        async with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM execution_attempts WHERE execution_id = ? ORDER BY attempt_number",
                (execution_id,),
            ).fetchall()
            return [
                ExecutionAttempt(
                    attempt_id=row["attempt_id"],
                    execution_id=row["execution_id"],
                    attempt_number=row["attempt_number"],
                    node_id=row["node_id"],
                    status=row["status"],
                    started_at=_parse_time(row["started_at"]),
                    finished_at=(_parse_time(row["finished_at"]) if row["finished_at"] else None),
                    error=row["error"],
                )
                for row in rows
            ]
