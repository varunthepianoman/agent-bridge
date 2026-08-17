"""Configuration for the native node process."""

from __future__ import annotations

import fnmatch
import os
import platform
import socket
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from urllib.parse import urlparse


def _csv(name: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in os.environ.get(name, "").split(",") if value.strip())


@dataclass(frozen=True, slots=True)
class ExclusionRules:
    """Local allow-by-default filters applied before data leaves a node."""

    providers: tuple[str, ...] = ()
    repositories: tuple[str, ...] = ()
    folders: tuple[str, ...] = ()
    conversations: tuple[str, ...] = ()
    include_transcripts: bool = True

    def excludes(
        self,
        *,
        provider: str,
        provider_thread_id: str,
        cwd: str | None,
        repository: str | None,
    ) -> bool:
        return (
            _matches(provider, self.providers)
            or _matches(provider_thread_id, self.conversations)
            or _matches(repository, self.repositories)
            or _path_matches(cwd, self.folders)
        )


def _matches(value: str | None, patterns: tuple[str, ...]) -> bool:
    if value is None:
        return False
    folded = value.casefold()
    return any(fnmatch.fnmatchcase(folded, pattern.casefold()) for pattern in patterns)


def _path_matches(value: str | None, patterns: tuple[str, ...]) -> bool:
    if value is None:
        return False
    # PurePath is intentionally not used to resolve or stat remote-looking paths.
    normalized = str(PurePath(value)).replace("\\", "/").casefold().rstrip("/")
    return any(
        fnmatch.fnmatchcase(normalized, pattern.replace("\\", "/").casefold().rstrip("/"))
        or normalized.startswith(pattern.replace("\\", "/").casefold().rstrip("/") + "/")
        for pattern in patterns
    )


@dataclass(frozen=True, slots=True)
class NodeAgentSettings:
    hub_url: str
    token: str
    node_id: str = field(default_factory=socket.gethostname)
    node_name: str = field(default_factory=socket.gethostname)
    environment_id: str = "host"
    environment_kind: str = field(default_factory=lambda: platform.system().casefold())
    interval_seconds: float = 10.0
    request_timeout_seconds: float = 30.0
    native_launch_enabled: bool = False
    codex_bin: str = "codex"
    claude_bin: str = "claude"
    exclusions: ExclusionRules = field(default_factory=ExclusionRules)

    def __post_init__(self) -> None:
        parsed = urlparse(self.hub_url)
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("hub_url must use HTTPS except for loopback development")
        if not self.token:
            raise ValueError("a non-empty node token is required")
        if self.interval_seconds <= 0 or self.request_timeout_seconds <= 0:
            raise ValueError("interval and request timeout must be positive")

    @classmethod
    def from_environment(cls) -> NodeAgentSettings:
        hub_url = os.environ.get("AGENT_BRIDGE_HUB_URL")
        token = os.environ.get("AGENT_BRIDGE_NODE_TOKEN")
        if not hub_url or not token:
            raise ValueError("AGENT_BRIDGE_HUB_URL and AGENT_BRIDGE_NODE_TOKEN are required")
        return cls(
            hub_url=hub_url.rstrip("/"),
            token=token,
            node_id=os.environ.get("AGENT_BRIDGE_NODE_ID", socket.gethostname()),
            node_name=os.environ.get("AGENT_BRIDGE_NODE_NAME", socket.gethostname()),
            environment_id=os.environ.get("AGENT_BRIDGE_ENVIRONMENT_ID", "host"),
            environment_kind=os.environ.get(
                "AGENT_BRIDGE_ENVIRONMENT_KIND", platform.system().casefold()
            ),
            interval_seconds=float(os.environ.get("AGENT_BRIDGE_NODE_INTERVAL", "10")),
            request_timeout_seconds=float(os.environ.get("AGENT_BRIDGE_HTTP_TIMEOUT", "30")),
            native_launch_enabled=os.environ.get("AGENT_BRIDGE_NATIVE_LAUNCH", "0") == "1",
            codex_bin=os.environ.get("AGENT_BRIDGE_CODEX_BIN", "codex"),
            claude_bin=os.environ.get("AGENT_BRIDGE_CLAUDE_BIN", "claude"),
            exclusions=ExclusionRules(
                providers=_csv("AGENT_BRIDGE_EXCLUDE_PROVIDERS"),
                repositories=_csv("AGENT_BRIDGE_EXCLUDE_REPOSITORIES"),
                folders=_csv("AGENT_BRIDGE_EXCLUDE_FOLDERS"),
                conversations=_csv("AGENT_BRIDGE_EXCLUDE_CONVERSATIONS"),
                include_transcripts=os.environ.get("AGENT_BRIDGE_SYNC_TRANSCRIPTS", "1") == "1",
            ),
        )

    @property
    def working_directory(self) -> Path:
        return Path.cwd()
