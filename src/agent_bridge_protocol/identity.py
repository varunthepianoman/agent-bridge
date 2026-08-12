"""Deterministic identifiers shared by Catalog and Bridge components."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

_ID_NAMESPACE = uuid.UUID("84b81388-81fd-4d66-9e26-a059a3ab8469")
_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def _canonical_part(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical_part(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical_part(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def stable_id(prefix: str, *identity_parts: Any) -> str:
    """Return a stable, opaque ID from an entity prefix and natural key parts.

    Parts are encoded as canonical JSON so boundaries and mapping order cannot
    create accidental collisions. The result is intentionally safe in URLs and
    NATS subject tokens.
    """

    if not _PREFIX_RE.fullmatch(prefix):
        raise ValueError("prefix must be 2-32 lowercase letters, digits, or underscores")
    if not identity_parts:
        raise ValueError("at least one identity part is required")
    encoded = json.dumps(
        [_canonical_part(part) for part in identity_parts],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return f"{prefix}-{uuid.uuid5(_ID_NAMESPACE, prefix + ':' + encoded).hex}"


def conversation_id(
    *,
    provider: str,
    provider_thread_id: str,
    node_id: str,
    environment_id: str | None = None,
) -> str:
    """Create the Catalog ID for one provider thread in one environment."""

    return stable_id(
        "conv",
        provider.strip().lower(),
        provider_thread_id.strip(),
        node_id.strip(),
        (environment_id or "").strip(),
    )


def provider_session_key(
    *,
    provider: str,
    provider_thread_id: str,
    node_id: str,
    environment_id: str | None = None,
) -> str:
    """Return the natural-key digest used to deduplicate session imports."""

    return stable_id(
        "session",
        provider.strip().lower(),
        provider_thread_id.strip(),
        node_id.strip(),
        (environment_id or "").strip(),
    )
