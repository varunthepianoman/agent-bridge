from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from agent_bridge_catalog.runtime import ConversationRuntime


class RecordingCodex:
    def __init__(self, *, turn_status: str = "completed") -> None:
        self.turn_status = turn_status
        self.resumed: list[str] = []
        self.unsubscribed: list[str] = []
        self.released = asyncio.Event()

    async def close(self) -> None:
        return None

    async def start_thread(self, *, cwd: str, model: str | None = None) -> Mapping[str, Any]:
        del cwd, model
        return {"id": "thread-new"}

    async def resume_thread(
        self, thread_id: str, *, cwd: str | None = None
    ) -> Mapping[str, Any]:
        del cwd
        self.resumed.append(thread_id)
        return {"id": thread_id}

    async def start_turn(
        self,
        thread_id: str,
        prompt: str,
        *,
        model: str | None = None,
        effort: str | None = None,
    ) -> Mapping[str, Any]:
        del prompt, model, effort
        return {
            "id": f"turn-{thread_id}",
            "status": self.turn_status,
            "error": "intentional failure" if self.turn_status == "failed" else None,
        }

    async def unsubscribe_thread(self, thread_id: str) -> str:
        self.unsubscribed.append(thread_id)
        self.released.set()
        return "unsubscribed"


def runtime_with(codex: RecordingCodex) -> ConversationRuntime:
    runtime = ConversationRuntime()
    runtime.codex = codex  # type: ignore[assignment]
    return runtime


async def test_resumed_thread_is_released_after_successful_turn() -> None:
    codex = RecordingCodex()
    runtime = runtime_with(codex)

    await runtime.turn(
        provider="codex",
        provider_thread_id="thread-existing",
        cwd="/tmp",
        prompt="ping",
    )

    assert codex.resumed == ["thread-existing"]
    assert codex.unsubscribed == ["thread-existing"]


async def test_resumed_thread_is_released_after_failed_turn() -> None:
    codex = RecordingCodex(turn_status="failed")
    runtime = runtime_with(codex)

    with pytest.raises(RuntimeError, match="intentional failure"):
        await runtime.turn(
            provider="codex",
            provider_thread_id="thread-existing",
            cwd="/tmp",
            prompt="ping",
        )

    assert codex.unsubscribed == ["thread-existing"]


async def test_new_thread_is_released_after_background_turn(tmp_path: Any) -> None:
    codex = RecordingCodex()
    runtime = runtime_with(codex)

    thread_id = await runtime.start(provider="codex", cwd=str(tmp_path), prompt="ping")
    await asyncio.wait_for(codex.released.wait(), timeout=1)

    assert thread_id == "thread-new"
    assert codex.unsubscribed == ["thread-new"]
