from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from agent_bridge_bridge.execution_store import SQLiteExecutionStore
from agent_bridge_bridge.runner_service import RunnerConfig, RunnerService, _transport_settings


def test_runner_config_is_strict_and_credentials_stay_outside_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "runner.json"
    path.write_text(
        json.dumps(
            {
                "node_id": "robotstudio-windows",
                "state_path": str(tmp_path / "runner.db"),
                "capabilities": {
                    "robot-simulator-e2e": {
                        "argv": ["pwsh", "-NoProfile", "-File", "C:/bridge/run.ps1"],
                        "allowed_workspaces": ["C:/bridge"],
                        "timeout_seconds": 900,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    config = RunnerConfig.load(path)
    assert config.node_id == "robotstudio-windows"
    assert list(config.capabilities) == ["robot-simulator-e2e"]

    monkeypatch.setenv("AGENT_BRIDGE_NATS_SERVERS", "nats://one:4222,nats://two:4222")
    monkeypatch.setenv("AGENT_BRIDGE_NATS_USERNAME", "robot-node")
    monkeypatch.setenv("AGENT_BRIDGE_NATS_PASSWORD", "secret-from-environment")
    transport = _transport_settings(config.node_id)
    assert transport.servers == ("nats://one:4222", "nats://two:4222")
    assert transport.username == "robot-node"
    assert "secret-from-environment" not in path.read_text(encoding="utf-8")


def test_runner_config_supports_codex_only_node(tmp_path: Path) -> None:
    config = RunnerConfig(
        node_id="local-codex",
        state_path=tmp_path / "codex-runner.db",
        codex={
            "model": "gpt-5.6-terra",
            "sandbox": "workspace_write",
            "config": {"sandbox_workspace_write": {"network_access": True}},
        },
    )
    assert config.capabilities == {}
    assert config.codex is not None
    assert config.codex.model == "gpt-5.6-terra"
    assert config.codex.config["sandbox_workspace_write"]["network_access"] is True

    with pytest.raises(ValueError, match="native capability or Codex"):
        RunnerConfig(node_id="empty")


class _EmptySubscription:
    async def fetch(self, *, batch: int, timeout: float) -> list[Any]:
        await asyncio.sleep(0)
        return []


class _FakeTransport:
    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.subscriptions: list[tuple[str, str, float]] = []

    async def connect(self) -> None:
        self.connected = True

    async def subscribe(
        self, subject: str, *, durable_name: str, ack_wait_seconds: float
    ) -> _EmptySubscription:
        self.subscriptions.append((subject, durable_name, ack_wait_seconds))
        return _EmptySubscription()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_service_subscribes_capability_node_and_control_subjects(tmp_path: Path) -> None:
    config = RunnerConfig(
        node_id="robot-node",
        state_path=tmp_path / "runner.db",
        capabilities={
            "robot-simulator-e2e": {
                "argv": ["pwsh", "-File", "run.ps1"],
            }
        },
    )
    transport = _FakeTransport()
    stop = asyncio.Event()
    stop.set()
    service = RunnerService(
        config=config,
        transport=transport,  # type: ignore[arg-type]
        store=SQLiteExecutionStore(tmp_path / "runner.db"),
    )

    await service.serve(stop)

    assert transport.connected is True
    assert transport.closed is True
    assert {item[0] for item in transport.subscriptions} == {
        "bridge.v1.capability.robot-simulator-e2e",
        "bridge.v1.control.capability.robot-simulator-e2e",
        "bridge.v1.inbox.node.robot-node",
        "bridge.v1.control.node.robot-node",
    }
