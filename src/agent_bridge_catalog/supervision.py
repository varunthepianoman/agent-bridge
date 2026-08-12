"""Small background-task supervisor used by health and diagnostics APIs."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class _TaskRecord:
    name: str
    task: asyncio.Task[Any]
    critical: bool
    started_at: datetime
    stopped_at: datetime | None = None
    error: str | None = None


class BackgroundSupervisor:
    def __init__(self) -> None:
        self._records: dict[str, _TaskRecord] = {}
        self._stopping = False

    def create_task(
        self,
        awaitable: Coroutine[Any, Any, Any],
        *,
        name: str,
        critical: bool = True,
    ) -> asyncio.Task[Any]:
        incumbent = self._records.get(name)
        if incumbent is not None and not incumbent.task.done():
            raise RuntimeError(f"background task {name!r} is already running")
        task = asyncio.create_task(awaitable, name=name)
        record = _TaskRecord(
            name=name,
            task=task,
            critical=critical,
            started_at=datetime.now(UTC),
        )
        self._records[name] = record
        task.add_done_callback(lambda completed: self._completed(record, completed))
        return task

    def _completed(self, record: _TaskRecord, task: asyncio.Task[Any]) -> None:
        record.stopped_at = datetime.now(UTC)
        if task.cancelled():
            if not self._stopping:
                record.error = "task cancelled unexpectedly"
            return
        error = task.exception()
        if error is not None:
            record.error = f"{type(error).__name__}: {error}"
        elif not self._stopping:
            record.error = "task exited unexpectedly"

    @property
    def degraded(self) -> bool:
        return any(
            record.critical and record.error is not None for record in self._records.values()
        )

    def snapshot(self) -> dict[str, Any]:
        items = []
        for record in sorted(self._records.values(), key=lambda item: item.name):
            if not record.task.done():
                state = "running"
            elif record.error:
                state = "failed"
            else:
                state = "stopped"
            items.append(
                {
                    "name": record.name,
                    "state": state,
                    "critical": record.critical,
                    "error": record.error,
                    "started_at": record.started_at.isoformat(),
                    "stopped_at": (record.stopped_at.isoformat() if record.stopped_at else None),
                }
            )
        return {
            "status": "degraded" if self.degraded else "healthy",
            "tasks": items,
        }

    async def stop(self) -> None:
        self._stopping = True
        tasks = [record.task for record in self._records.values() if not record.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
