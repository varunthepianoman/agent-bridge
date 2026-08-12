"""Command line entrypoint for the native node process."""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import suppress
from dataclasses import asdict

from agent_bridge_providers import (
    ClaudeCatalogAdapter,
    CodexCatalogAdapter,
    CompositeCatalogAdapter,
)

from .agent import NodeAgent
from .config import NodeAgentSettings
from .hub import HubClient
from .runner import NativeCommandRunner


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
    provider = CompositeCatalogAdapter(
        [
            CodexCatalogAdapter(codex_bin=settings.codex_bin),
            ClaudeCatalogAdapter(claude_bin=settings.claude_bin),
        ]
    )
    runner = NativeCommandRunner(
        environment_id=settings.environment_id,
        enabled=settings.native_launch_enabled,
        codex_bin=settings.codex_bin,
        claude_bin=settings.claude_bin,
        platform_name=settings.environment_kind,
    )
    agent = NodeAgent(settings, hub, provider, runner)
    try:
        if once:
            print(json.dumps(asdict(await agent.run_once()), sort_keys=True))
        else:
            await agent.serve()
    finally:
        await provider.close()
        hub.close()


def main() -> None:
    arguments = parser().parse_args()
    with suppress(KeyboardInterrupt):
        asyncio.run(_run(once=arguments.once))


if __name__ == "__main__":
    main()
