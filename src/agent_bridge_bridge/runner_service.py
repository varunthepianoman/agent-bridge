"""Headless JetStream execution runner for native nodes and test capabilities."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_bridge_protocol.models import EndpointKind, EndpointRef, ExecutionRequest

from .execution_store import SQLiteExecutionStore
from .runners import (
    AllowedCommand,
    AllowlistedCommandRunner,
    CancellationToken,
    CodexSDKRunner,
    ExecutionDispatcher,
    OpenAICodexClient,
    RegisteredCapabilityRunner,
    RunnerOutput,
    UnsupportedExecution,
)
from .subjects import capability_subject, control_subject, inbox_subject
from .transport import BridgeSubscription, JetStreamSettings, JetStreamTransport
from .worker import ControlWorker, ExecutionWorker, WorkerSettings


class CommandConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    argv: list[str] = Field(min_length=1)
    allowed_workspaces: list[Path] = Field(default_factory=list)
    timeout_seconds: float = Field(default=600, gt=0)


class CodexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    sandbox: str = "workspace_write"
    approval_mode: str = "deny_all"
    config: dict[str, Any] = Field(default_factory=dict)


class RunnerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    worker_id: str | None = None
    state_path: Path | None = None
    capabilities: dict[str, CommandConfig] = Field(default_factory=dict)
    codex: CodexConfig | None = None
    lease_seconds: float = Field(default=60, gt=0)
    lease_renewal_seconds: float = Field(default=20, gt=0)
    retry_backoff_seconds: float = Field(default=5, ge=0)
    fetch_timeout_seconds: float = Field(default=1, gt=0)

    @model_validator(mode="after")
    def validate_lease(self) -> RunnerConfig:
        if self.lease_renewal_seconds >= self.lease_seconds:
            raise ValueError("lease_renewal_seconds must be shorter than lease_seconds")
        # Validate capability identities with the canonical subject grammar.
        for capability in self.capabilities:
            capability_subject(capability)
        if not self.capabilities and self.codex is None:
            raise ValueError("runner requires at least one native capability or Codex")
        return self

    @classmethod
    def load(cls, path: Path) -> RunnerConfig:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class _UnsupportedRunner:
    async def run(self, *_args: Any, **_kwargs: Any) -> RunnerOutput:
        raise UnsupportedExecution("this runner only accepts registered native capabilities")


def _capability_handlers(
    config: RunnerConfig, command_runner: AllowlistedCommandRunner
) -> dict[str, Callable[[ExecutionRequest, CancellationToken], Awaitable[RunnerOutput]]]:
    result: dict[str, Callable[[ExecutionRequest, CancellationToken], Awaitable[RunnerOutput]]] = {}
    for capability in config.capabilities:

        async def invoke(
            request: ExecutionRequest,
            cancellation: CancellationToken,
            *,
            command_id: str = capability,
        ) -> RunnerOutput:
            parameters = dict(request.parameters)
            parameters["command_id"] = command_id
            command_request = request.model_copy(update={"parameters": parameters})

            async def ignore_progress(_summary: str, _percent: float | None) -> None:
                return None

            output = await command_runner.run(command_request, cancellation, ignore_progress)
            stdout = output.output.get("stdout")
            if isinstance(stdout, str):
                try:
                    structured = json.loads(stdout)
                except json.JSONDecodeError:
                    pass
                else:
                    return RunnerOutput(
                        summary=output.summary,
                        output={**output.output, "structured_result": structured},
                    )
            return output

        result[capability] = invoke
    return result


@dataclass
class RunnerService:
    config: RunnerConfig
    transport: JetStreamTransport
    store: SQLiteExecutionStore

    async def serve(self, stop: asyncio.Event | None = None) -> None:
        stop_event = stop or asyncio.Event()
        commands = {
            name: AllowedCommand(
                argv=tuple(item.argv),
                allowed_workspaces=tuple(item.allowed_workspaces),
                timeout_seconds=item.timeout_seconds,
            )
            for name, item in self.config.capabilities.items()
        }
        command_runner = AllowlistedCommandRunner(commands)
        unsupported = _UnsupportedRunner()
        codex = (
            CodexSDKRunner(
                OpenAICodexClient(
                    model=self.config.codex.model,
                    sandbox=self.config.codex.sandbox,
                    approval_mode=self.config.codex.approval_mode,
                    config=self.config.codex.config,
                )
            )
            if self.config.codex is not None
            else unsupported
        )
        dispatcher = ExecutionDispatcher(
            codex=codex,
            wake=unsupported,
            commands=unsupported,
            capabilities=RegisteredCapabilityRunner(
                _capability_handlers(self.config, command_runner)
            ),
        )
        execution_worker = ExecutionWorker(
            settings=WorkerSettings(
                worker_id=self.config.worker_id or f"runner-{self.config.node_id}",
                node_id=self.config.node_id,
                lease_seconds=self.config.lease_seconds,
                lease_renewal_seconds=self.config.lease_renewal_seconds,
                retry_backoff_seconds=self.config.retry_backoff_seconds,
            ),
            transport=self.transport,
            store=self.store,
            dispatcher=dispatcher,
        )
        control_worker = ControlWorker(self.store)
        await self.transport.connect()
        subscriptions: list[tuple[BridgeSubscription, ExecutionWorker | ControlWorker]] = []
        for capability in self.config.capabilities:
            safe = capability.replace("_", "-")
            subscriptions.append(
                (
                    await self.transport.subscribe(
                        capability_subject(capability),
                        durable_name=f"capability-{safe}-v1",
                        ack_wait_seconds=self.config.lease_seconds,
                    ),
                    execution_worker,
                )
            )
            capability_endpoint = EndpointRef(kind=EndpointKind.CAPABILITY, id=capability)
            subscriptions.append(
                (
                    await self.transport.subscribe(
                        control_subject(capability_endpoint),
                        # Control is fan-out per registered node, unlike competing
                        # capability work. Whichever node owns the execution sees the
                        # durable cancellation, including after an offline restart.
                        durable_name=(f"control-capability-{safe}-node-{self.config.node_id}-v1"),
                        ack_wait_seconds=self.config.lease_seconds,
                    ),
                    control_worker,
                )
            )
        node_endpoint = EndpointRef(kind=EndpointKind.NODE, id=self.config.node_id)
        subscriptions.extend(
            [
                (
                    await self.transport.subscribe(
                        inbox_subject(node_endpoint),
                        durable_name=f"inbox-node-{self.config.node_id}-v1",
                        ack_wait_seconds=self.config.lease_seconds,
                    ),
                    execution_worker,
                ),
                (
                    await self.transport.subscribe(
                        control_subject(node_endpoint),
                        durable_name=f"control-node-{self.config.node_id}-v1",
                        ack_wait_seconds=self.config.lease_seconds,
                    ),
                    control_worker,
                ),
            ]
        )

        async def consume(
            subscription: BridgeSubscription, worker: ExecutionWorker | ControlWorker
        ) -> None:
            while not stop_event.is_set():
                await worker.run_once(subscription, timeout=self.config.fetch_timeout_seconds)

        tasks = [asyncio.create_task(consume(*item)) for item in subscriptions]
        try:
            await stop_event.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.transport.close()
            self.store.close()


def _transport_settings(node_id: str) -> JetStreamSettings:
    servers = tuple(
        item.strip()
        for item in os.environ.get("AGENT_BRIDGE_NATS_SERVERS", "nats://127.0.0.1:4222").split(",")
        if item.strip()
    )
    credentials = os.environ.get("AGENT_BRIDGE_NATS_CREDENTIALS_FILE")
    return JetStreamSettings(
        servers=servers,
        client_name=f"agent-bridge-runner-{node_id}",
        credentials_file=Path(credentials) if credentials else None,
        username=os.environ.get("AGENT_BRIDGE_NATS_USERNAME"),
        password=os.environ.get("AGENT_BRIDGE_NATS_PASSWORD"),
    )


async def _serve(config_path: Path) -> None:
    config = RunnerConfig.load(config_path)
    state_path = config.state_path or Path.home() / ".local/state/agent-bridge/runner.db"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(event, stop.set)
    service = RunnerService(
        config=config,
        transport=JetStreamTransport(_transport_settings(config.node_id)),
        store=SQLiteExecutionStore(state_path),
    )
    await service.serve(stop)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-bridge-runner")
    command = parser.add_subparsers(dest="command", required=True)
    serve = command.add_parser("serve", help="consume durable Bridge work")
    serve.add_argument(
        "--config",
        type=Path,
        default=(Path(value) if (value := os.environ.get("AGENT_BRIDGE_RUNNER_CONFIG")) else None),
        required=os.environ.get("AGENT_BRIDGE_RUNNER_CONFIG") is None,
    )
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    if arguments.command != "serve" or arguments.config is None:
        raise SystemExit("serve requires --config or AGENT_BRIDGE_RUNNER_CONFIG")
    try:
        asyncio.run(_serve(arguments.config))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
