"""One-cycle and daemon orchestration for the native node process."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .config import ExclusionRules, NodeAgentSettings
from .hub import HubTransportError
from .runner import CommandResult, NodeCommand, NodeTurnEvent

LOGGER = logging.getLogger(__name__)
_MAX_RETRY_DELAY_SECONDS = 60
NODE_CAPABILITIES = [
    "catalog.collect",
    "conversation.read",
    "native.open",
    "native.resume",
]


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

    def claim_commands(
        self,
        node_id: str,
        *,
        provider_capacity_available: bool = True,
        active_provider_conversations: Sequence[str] = (),
    ) -> list[NodeCommand]: ...

    def report_result(self, node_id: str, result: CommandResult) -> Mapping[str, Any]: ...

    def report_turn_event(self, node_id: str, event: NodeTurnEvent) -> Mapping[str, Any]: ...


class CommandRunner(Protocol):
    async def execute(self, request: NodeCommand) -> CommandResult: ...


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
        self._pending_results: list[CommandResult] = []
        self._pending_turn_events: list[NodeTurnEvent] = []
        self._command_tasks: dict[asyncio.Task[None], NodeCommand] = {}
        self._provider_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._provider_semaphore = asyncio.Semaphore(settings.max_provider_concurrency)
        self._provider_command_states: dict[str, str] = {}
        self._provider_commands: dict[str, NodeCommand] = {}

    def queue_turn_event(self, event: NodeTurnEvent) -> None:
        self._pending_turn_events.append(event)

    def flush_pending(self) -> None:
        self._report_pending_results()
        self._report_pending_turn_events()

    async def run_once(self, *, background_commands: bool = False) -> NodeCycleResult:
        self.flush_pending()
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
            "capabilities": NODE_CAPABILITIES,
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
        commands = self.hub.claim_commands(
            self.settings.node_id,
            provider_capacity_available=(
                len(self._provider_commands) < self.settings.max_provider_concurrency
            ),
            active_provider_conversations=sorted(
                {
                    identity
                    for command in self._provider_commands.values()
                    if (identity := self._conversation_identity(command)) is not None
                }
            ),
        )
        failed = 0
        for command in commands:
            if background_commands:
                self._start_command(command)
            else:
                result = await self._execute_command(command)
                failed += result.status != "succeeded"
                self._record_result(result)
        heartbeat_ttl = max(15, min(600, round(self.settings.interval_seconds * 3)))
        self.hub.heartbeat(
            {
                "node_id": self.settings.node_id,
                "ttl_seconds": heartbeat_ttl,
                "capabilities": NODE_CAPABILITIES,
                "metadata": self._provider_status(),
            }
        )
        return NodeCycleResult(discovered, len(records), excluded, len(commands), failed)

    def _start_command(self, command: NodeCommand) -> None:
        self._register_provider_command(command)
        task = asyncio.create_task(
            self._run_background_command(command),
            name=f"node-command-{command.command_id}",
        )
        self._command_tasks[task] = command
        task.add_done_callback(self._command_finished)

    async def _run_background_command(self, command: NodeCommand) -> None:
        result = await self._execute_command(command)
        self._record_result(result)

    async def _execute_command(self, command: NodeCommand) -> CommandResult:
        heartbeat = (
            asyncio.create_task(self._heartbeat_while_busy())
            if self._is_provider_command(command)
            else None
        )
        try:
            if self._is_provider_command(command):
                self._register_provider_command(command)
                lock = self._provider_locks.setdefault(
                    self._provider_lock_key(command), asyncio.Lock()
                )
                async with lock:
                    self._provider_command_states[command.command_id] = "concurrency_limit"
                    async with self._provider_semaphore:
                        self._provider_command_states[command.command_id] = "active"
                        return await self.runner.execute(command)
            return await self.runner.execute(command)
        finally:
            self._release_provider_command(command)
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    def _register_provider_command(self, command: NodeCommand) -> None:
        if not self._is_provider_command(command):
            return
        self._provider_commands.setdefault(command.command_id, command)
        self._provider_command_states.setdefault(command.command_id, "conversation_lock")

    def _release_provider_command(self, command: NodeCommand) -> None:
        self._provider_command_states.pop(command.command_id, None)
        self._provider_commands.pop(command.command_id, None)

    def _record_result(self, result: CommandResult) -> None:
        self._pending_results.append(result)
        self._report_pending_results()
        self._report_pending_turn_events()

    def _command_finished(self, task: asyncio.Task[None]) -> None:
        command = self._command_tasks.pop(task)
        self._release_provider_command(command)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            LOGGER.error(
                "Node command %s failed before reporting a result",
                command.command_id,
                exc_info=(type(error), error, error.__traceback__),
            )

    @staticmethod
    def _is_provider_command(command: NodeCommand) -> bool:
        return command.kind in {"start_conversation", "deliver_turn"}

    @staticmethod
    def _conversation_identity(command: NodeCommand) -> str | None:
        if command.conversation_id:
            return command.conversation_id
        if command.provider_thread_id:
            return f"{command.provider}:{command.provider_thread_id}"
        return None

    @classmethod
    def _provider_lock_key(cls, command: NodeCommand) -> tuple[str, str]:
        identity = cls._conversation_identity(command) or f"command:{command.command_id}"
        return command.provider, identity

    def _provider_status(self) -> dict[str, Any]:
        active = [
            command
            for command_id, command in self._provider_commands.items()
            if self._provider_command_states.get(command_id) == "active"
        ]
        waiting = {
            command_id: state
            for command_id, state in self._provider_command_states.items()
            if state != "active"
        }
        return {
            "busy": bool(self._provider_commands),
            "active_provider_command_count": len(active),
            "waiting_provider_command_count": len(waiting),
            "active_conversation_ids": sorted(
                {
                    identity
                    for command in active
                    if (identity := self._conversation_identity(command)) is not None
                }
            ),
            "provider_command_waits": waiting,
        }

    async def _heartbeat_while_busy(self) -> None:
        ttl = max(15, min(600, round(self.settings.interval_seconds * 3)))
        failures = 0
        while True:
            try:
                heartbeat = {
                    "node_id": self.settings.node_id,
                    "ttl_seconds": ttl,
                    "capabilities": NODE_CAPABILITIES,
                    "metadata": self._provider_status(),
                }
                await asyncio.to_thread(self.hub.heartbeat, heartbeat)
            except HubTransportError as error:
                failures += 1
                delay = self._retry_delay(failures)
                LOGGER.warning("Busy heartbeat failed; retrying in %s seconds: %s", delay, error)
            else:
                failures = 0
                delay = self.settings.interval_seconds
            await asyncio.sleep(delay)

    async def serve(self) -> None:
        failures = 0
        try:
            while True:
                try:
                    await self.run_once(background_commands=True)
                except HubTransportError as error:
                    failures += 1
                    delay = self._retry_delay(failures)
                    LOGGER.warning(
                        "Hub communication failed; retrying in %s seconds: %s", delay, error
                    )
                    await asyncio.sleep(delay)
                else:
                    failures = 0
                    await asyncio.sleep(self.settings.interval_seconds)
        finally:
            tasks = list(self._command_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _report_pending_results(self) -> None:
        while self._pending_results:
            result = self._pending_results[0]
            self.hub.report_result(self.settings.node_id, result)
            self._pending_results.pop(0)

    def _report_pending_turn_events(self) -> None:
        while self._pending_turn_events:
            event = self._pending_turn_events[0]
            self.hub.report_turn_event(self.settings.node_id, event)
            self._pending_turn_events.pop(0)

    def _retry_delay(self, failures: int) -> float:
        return float(
            min(
                _MAX_RETRY_DELAY_SECONDS,
                max(self.settings.interval_seconds, 2 ** min(failures - 1, 6)),
            )
        )


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
