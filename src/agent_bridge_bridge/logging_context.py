"""Context-local structured identifiers for logs and telemetry exporters."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_FIELDS = (
    "conversation_id",
    "role_id",
    "work_id",
    "message_id",
    "execution_id",
    "correlation_id",
    "node_id",
)
_context: ContextVar[dict[str, str] | None] = ContextVar("agent_bridge_log_context", default=None)


@contextmanager
def bind_log_context(**values: str | None) -> Iterator[None]:
    selected = {key: value for key, value in values.items() if key in _FIELDS and value}
    token = _context.set({**(_context.get() or {}), **selected})
    try:
        yield
    finally:
        _context.reset(token)


def structured_extra(**values: str | None) -> dict[str, Any]:
    selected = {key: value for key, value in values.items() if key in _FIELDS and value}
    return {"agent_bridge": {**(_context.get() or {}), **selected}}


def current_log_context() -> dict[str, str]:
    return dict(_context.get() or {})
