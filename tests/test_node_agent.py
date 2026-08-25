from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

from agent_bridge_node.agent import NodeAgent
from agent_bridge_node.config import ExclusionRules, NodeAgentSettings
from agent_bridge_node.hub import HubTransportError
from agent_bridge_node.runner import CommandResult, NodeCommand
from agent_bridge_providers.codex import DiscoveredConversation


class FakeProvider:
    def __init__(self, records: list[DiscoveredConversation]) -> None:
        self.records = records
        self.include_turns: bool | None = None

    async def discover(
        self, *, include_turns: bool = True
    ) -> AsyncIterator[DiscoveredConversation]:
        self.include_turns = include_turns
        for record in self.records:
            yield record


class FakeHub:
    def __init__(self, commands: list[NodeCommand] | None = None) -> None:
        self.commands = commands or []
        self.synced: list[dict[str, Any]] = []
        self.registration: dict[str, Any] | None = None
        self.environments: list[dict[str, Any]] = []
        self.results: list[CommandResult] = []
        self.beats: list[dict[str, Any]] = []

    def synchronize(
        self,
        registration: dict[str, Any],
        conversations: list[dict[str, Any]],
        environments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.registration = registration
        self.synced = conversations
        self.environments = environments
        return {}

    def claim_commands(self, node_id: str) -> list[NodeCommand]:
        return self.commands

    def report_result(self, node_id: str, result: CommandResult) -> dict[str, Any]:
        self.results.append(result)
        return {}

    def heartbeat(self, heartbeat: dict[str, Any]) -> dict[str, Any]:
        self.beats.append(heartbeat)
        return {}


@dataclass
class FakeRunner:
    def execute(self, request: NodeCommand) -> CommandResult:
        return CommandResult(
            command_id=request.command_id,
            claim_token=request.claim_token,
            status="failed",
            detail="unavailable",
        )


def conversation(thread_id: str, *, repository: str = "public") -> DiscoveredConversation:
    return DiscoveredConversation(
        provider="codex",
        provider_thread_id=thread_id,
        title=thread_id,
        preview="preview",
        cwd="/workspace",
        source_kind="cli",
        model_provider="openai",
        created_at=1,
        updated_at=2,
        status="idle",
        git_origin_url=repository,
        transcript_text="user: private words",
    )


async def test_cycle_filters_locally_syncs_heartbeat_and_reports_commands() -> None:
    settings = NodeAgentSettings(
        hub_url="http://localhost:8000",
        token="node-token",
        node_id="node-a",
        environment_id="wsl-a",
        environment_kind="wsl",
        exclusions=ExclusionRules(repositories=("secret",), include_transcripts=False),
    )
    provider = FakeProvider([conversation("visible"), conversation("hidden", repository="secret")])
    hub = FakeHub(
        [
            NodeCommand(
                command_id="cmd-1",
                claim_token="claim-1",
                kind="open_path",
                environment_id="wsl-a",
                path="/x",
            )
        ]
    )
    result = await NodeAgent(settings, hub, provider, FakeRunner()).run_once()

    assert result.discovered == 2
    assert result.synchronized == 1
    assert result.excluded == 1
    assert result.command_failures == 1
    assert provider.include_turns is False
    assert hub.synced[0]["provider_thread_id"] == "visible"
    assert hub.synced[0]["environment_id"] == "wsl-a"
    assert hub.synced[0]["transcript_text"] == ""
    assert hub.registration is not None
    assert hub.registration["node_id"] == "node-a"
    assert hub.environments[0]["environment_id"] == "wsl-a"
    assert hub.results[0].command_id == "cmd-1"
    assert hub.beats[0]["node_id"] == "node-a"


async def test_daemon_recovers_after_transient_hub_synchronization_failure(monkeypatch) -> None:
    class TransientHub(FakeHub):
        def __init__(self) -> None:
            super().__init__()
            self.sync_attempts = 0

        def synchronize(
            self,
            registration: dict[str, Any],
            conversations: list[dict[str, Any]],
            environments: list[dict[str, Any]],
        ) -> dict[str, Any]:
            self.sync_attempts += 1
            if self.sync_attempts == 1:
                raise HubTransportError("Hub transport failed (ConnectError)")
            return super().synchronize(registration, conversations, environments)

    sleeps = 0

    async def sleep(_delay: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr("agent_bridge_node.agent.asyncio.sleep", sleep)
    settings = NodeAgentSettings(
        hub_url="http://localhost:8000", token="token", interval_seconds=0.01
    )
    hub = TransientHub()
    agent = NodeAgent(settings, hub, FakeProvider([]), FakeRunner())

    with pytest.raises(asyncio.CancelledError):
        await agent.serve()

    assert hub.sync_attempts == 2


async def test_busy_heartbeat_transport_failure_does_not_stop_provider_execution() -> None:
    class BusyHeartbeatHub(FakeHub):
        def heartbeat(self, heartbeat: dict[str, Any]) -> dict[str, Any]:
            if heartbeat.get("metadata") == {"busy": True}:
                raise HubTransportError("Hub transport failed (ConnectError)")
            return super().heartbeat(heartbeat)

    @dataclass
    class SlowRunner:
        completed: bool = False

        def execute(self, request: NodeCommand) -> CommandResult:
            time.sleep(0.02)
            self.completed = True
            return CommandResult(
                command_id=request.command_id,
                claim_token=request.claim_token,
                status="succeeded",
            )

    settings = NodeAgentSettings(
        hub_url="http://localhost:8000", token="token", interval_seconds=0.001
    )
    command = NodeCommand(
        command_id="cmd-1",
        claim_token="claim-1",
        kind="open_path",
        environment_id="host",
        path="/x",
    )
    runner = SlowRunner()
    hub = BusyHeartbeatHub([command])

    result = await NodeAgent(settings, hub, FakeProvider([]), runner).run_once()

    assert runner.completed is True
    assert result.commands == 1
    assert hub.results[0].command_id == "cmd-1"


async def test_completed_result_is_retried_without_rerunning_provider() -> None:
    class RetryResultHub(FakeHub):
        def __init__(self, command: NodeCommand) -> None:
            super().__init__([command])
            self.report_attempts = 0

        def claim_commands(self, node_id: str) -> list[NodeCommand]:
            commands = self.commands
            self.commands = []
            return commands

        def report_result(self, node_id: str, result: CommandResult) -> dict[str, Any]:
            self.report_attempts += 1
            if self.report_attempts == 1:
                raise HubTransportError("Hub transport failed (ConnectError)")
            return super().report_result(node_id, result)

    @dataclass
    class CountingRunner:
        calls: int = 0

        def execute(self, request: NodeCommand) -> CommandResult:
            self.calls += 1
            return CommandResult(
                command_id=request.command_id,
                claim_token=request.claim_token,
                status="succeeded",
            )

    settings = NodeAgentSettings(
        hub_url="http://localhost:8000", token="token", interval_seconds=0.01
    )
    command = NodeCommand(
        command_id="cmd-1",
        claim_token="claim-1",
        kind="open_path",
        environment_id="host",
        path="/x",
    )
    hub = RetryResultHub(command)
    runner = CountingRunner()
    agent = NodeAgent(settings, hub, FakeProvider([]), runner)

    with pytest.raises(HubTransportError):
        await agent.run_once()
    await agent.run_once()

    assert runner.calls == 1
    assert hub.report_attempts == 2
    assert [result.command_id for result in hub.results] == ["cmd-1"]
