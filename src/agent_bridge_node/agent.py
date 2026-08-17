"""One-cycle and daemon orchestration for the native node process."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .config import ExclusionRules, NodeAgentSettings
from .runner import CommandResult, NodeCommand


class DiscoveredItem(Protocol):
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
    parent_thread_id: str | None
    git_sha: str | None
    git_branch: str | None
    git_origin_url: str | None
    is_pinned: bool
    is_ephemeral: bool
    is_archived: bool
    transcript_text: str
    raw_metadata: Mapping[str, Any]


class ConversationProvider(Protocol):
    def discover(self, *, include_turns: bool = True) -> AsyncIterator[Any]: ...


class NodeHub(Protocol):
    def synchronize(
        self,
        registration: Mapping[str, Any],
        conversations: Sequence[Mapping[str, Any]],
        environments: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]: ...

    def heartbeat(self, heartbeat: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def claim_commands(self, node_id: str) -> list[NodeCommand]: ...

    def report_result(self, node_id: str, result: CommandResult) -> Mapping[str, Any]: ...


class CommandRunner(Protocol):
    def execute(self, request: NodeCommand) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class NodeCycleResult:
    discovered: int
    synchronized: int
    excluded: int
    commands: int
    command_failures: int


class NodeAgent:
    def __init__(
        self,
        settings: NodeAgentSettings,
        hub: NodeHub,
        provider: ConversationProvider,
        runner: CommandRunner,
    ) -> None:
        self.settings = settings
        self.hub = hub
        self.provider = provider
        self.runner = runner

    async def run_once(self) -> NodeCycleResult:
        records: list[dict[str, Any]] = []
        discovered = excluded = 0
        rules = self.settings.exclusions
        async for item in self.provider.discover(include_turns=rules.include_transcripts):
            discovered += 1
            if rules.excludes(
                provider=item.provider,
                provider_thread_id=item.provider_thread_id,
                cwd=item.cwd,
                repository=item.git_origin_url,
            ):
                excluded += 1
                continue
            records.append(_serialize(item, rules, self.settings.environment_id))

        registration = {
            "node_id": self.settings.node_id,
            "display_name": self.settings.node_name,
            "platform": self.settings.environment_kind,
            "capabilities": ["catalog.collect", "native.resume", "native.open"],
        }
        environment = {
            "environment_id": self.settings.environment_id,
            "display_name": self.settings.environment_id,
            "kind": self.settings.environment_kind,
            "include_transcript_text": rules.include_transcripts,
            "exclude_providers": list(rules.providers),
            "exclude_repositories": list(rules.repositories),
            "exclude_folders": list(rules.folders),
            "exclude_conversation_ids": list(rules.conversations),
        }
        self.hub.synchronize(registration, records, [environment])
        commands = self.hub.claim_commands(self.settings.node_id)
        failed = 0
        for command in commands:
            heartbeat = asyncio.create_task(self._heartbeat_while_busy())
            try:
                result = await asyncio.to_thread(self.runner.execute, command)
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            failed += result.status == "failed"
            self.hub.report_result(self.settings.node_id, result)
        heartbeat_ttl = max(15, min(600, round(self.settings.interval_seconds * 3)))
        self.hub.heartbeat(
            {
                "node_id": self.settings.node_id,
                "ttl_seconds": heartbeat_ttl,
                "capabilities": ["catalog.collect", "native.resume", "native.open"],
            }
        )
        return NodeCycleResult(discovered, len(records), excluded, len(commands), failed)

    async def _heartbeat_while_busy(self) -> None:
        ttl = max(15, min(600, round(self.settings.interval_seconds * 3)))
        while True:
            await asyncio.to_thread(
                self.hub.heartbeat,
                {
                    "node_id": self.settings.node_id,
                    "ttl_seconds": ttl,
                    "capabilities": ["catalog.collect", "native.resume", "native.open"],
                    "metadata": {"busy": True},
                },
            )
            await asyncio.sleep(self.settings.interval_seconds)

    async def serve(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self.settings.interval_seconds)


def _serialize(item: DiscoveredItem, rules: ExclusionRules, environment_id: str) -> dict[str, Any]:
    return {
        "provider": item.provider,
        "provider_thread_id": item.provider_thread_id,
        "environment_id": environment_id,
        "title": item.title,
        "preview": item.preview,
        "cwd": item.cwd,
        "source_kind": item.source_kind,
        "model_provider": item.model_provider,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "status": item.status,
        "parent_thread_id": item.parent_thread_id,
        "git_sha": item.git_sha,
        "git_branch": item.git_branch,
        "git_origin_url": item.git_origin_url,
        "is_pinned": item.is_pinned,
        "is_ephemeral": item.is_ephemeral,
        "is_archived": item.is_archived,
        "transcript_text": item.transcript_text if rules.include_transcripts else "",
    }
