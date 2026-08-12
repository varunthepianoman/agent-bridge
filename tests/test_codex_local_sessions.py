from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent_bridge_providers.codex import LocalCodexSessionReader


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def test_local_fallback_indexes_only_user_and_agent_prose(tmp_path: Path) -> None:
    write_jsonl(
        tmp_path / "session_index.jsonl",
        [
            {
                "id": "thr-local",
                "thread_name": "Named reconnect investigation",
                "updated_at": "2026-01-01",
            }
        ],
    )
    write_jsonl(
        tmp_path / "sessions" / "2026" / "rollout.jsonl",
        [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": "thr-local",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "cwd": "/workspace/reconnect",
                    "source": "vscode",
                    "model_provider": "openai",
                    "git": {"branch": "feature/reconnect", "commit_hash": "abc123"},
                },
            },
            {
                "timestamp": "2026-01-01T00:01:00Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "Find the reconnect bug"},
            },
            {
                "timestamp": "2026-01-01T00:02:00Z",
                "type": "event_msg",
                "payload": {"type": "custom_tool_call_output", "output": "SECRET OUTPUT"},
            },
            {
                "timestamp": "2026-01-01T00:03:00Z",
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "Use a generation counter"},
            },
        ],
    )

    async def discover():
        return [item async for item in LocalCodexSessionReader(tmp_path).discover()]

    records = asyncio.run(discover())
    assert len(records) == 1
    assert records[0].title == "Named reconnect investigation"
    assert records[0].source_kind == "vscode"
    assert "Find the reconnect bug" in records[0].transcript_text
    assert "generation counter" in records[0].transcript_text
    assert "SECRET OUTPUT" not in records[0].transcript_text
