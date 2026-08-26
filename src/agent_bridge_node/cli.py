"""Command line entrypoint for the native node process."""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import suppress
from dataclasses import asdict

from agent_bridge_providers import (
    AppServerClient,
    ClaudeCatalogAdapter,
    CodexCatalogAdapter,
    CompositeCatalogAdapter,
)

from .agent import NodeAgent
from .claude_runtime import RemoteClaudeRuntime
from .codex_runtime import RemoteCodexRuntime
from .config import NodeAgentSettings
from .hub import HubClient
from .runner import NativeCommandRunner, RemoteCommandRunner


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="agent-bridge-node")
    result.add_argument("--once", action="store_true", help="synchronize one cycle and exit")
    return result


async def _run(*, once: bool) -> None:
    settings = NodeAgentSettings.from_environment()
    hub = HubClient(
        settings.hub_url,
        settings.token,
        timeout=settings.request_timeout_seconds,
    )
    codex_client = AppServerClient((settings.codex_bin, "app-server"))
    provider = CompositeCatalogAdapter(
        [
            CodexCatalogAdapter(codex_client, codex_bin=settings.codex_bin),
            ClaudeCatalogAdapter(claude_bin=settings.claude_bin),
        ]
    )
    native_runner = NativeCommandRunner(
        environment_id=settings.environment_id,
        codex_bin=settings.codex_bin,
        claude_bin=settings.claude_bin,
        platform_name=settings.environment_kind,
    )
    codex_runtime = RemoteCodexRuntime(
        codex_client,
        node_id=settings.node_id,
    )
    claude_runtime = RemoteClaudeRuntime(settings.claude_bin, node_id=settings.node_id)
    runner = RemoteCommandRunner(native_runner, codex_runtime, claude_runtime)
    agent = NodeAgent(settings, hub, provider, runner)
    codex_runtime.set_event_sink(agent.queue_turn_event)
    claude_runtime.set_event_sink(agent.queue_turn_event)
    try:
        if once:
            cycle = await agent.run_once()
            await codex_runtime.wait_for_background()
            await claude_runtime.wait_for_background()
            agent.flush_pending()
            print(json.dumps(asdict(cycle), sort_keys=True))
        else:
            await agent.serve()
    finally:
        await claude_runtime.close()
        await codex_runtime.close()
        await provider.close()
        await codex_client.close()
        hub.close()


def main() -> None:
    arguments = parser().parse_args()
    with suppress(KeyboardInterrupt):
        asyncio.run(_run(once=arguments.once))


if __name__ == "__main__":
    main()
