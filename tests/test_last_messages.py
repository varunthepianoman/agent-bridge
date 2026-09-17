from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_bridge_bridge.cli import run
from agent_bridge_catalog.app import create_app
from agent_bridge_catalog.config import Settings
from agent_bridge_mcp import server
from agent_bridge_providers.codex import CodexCatalogAdapter


class EmptyProvider:
    async def discover(self, *, include_turns: bool = True) -> AsyncIterator[Any]:
        if False:
            yield None

    async def close(self) -> None:
        pass


def test_last_messages_preserves_boundaries_and_does_not_leak_history(tmp_path: Path) -> None:
    app = create_app(
        settings=Settings(
            state_dir=tmp_path,
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            node_id="hub",
            environment_id="host",
            discovery_interval_seconds=3600,
        ),
        provider=EmptyProvider(),
    )
    messages = [
        {"role": "user", "text": "OLD_HISTORY"},
        {"role": "assistant", "text": "A multiline reply\nuser: quoted role, not a message"},
        {"role": "user", "text": "latest question"},
    ]
    payload = {
        "provider": "codex",
        "provider_thread_id": "thread-1",
        "preview": "OLD_HISTORY",
        "transcript_text": "OLD_HISTORY flattened",
        "transcript_messages": messages,
    }
    with TestClient(app) as client:
        repo = app.state.repository
        row = repo.upsert_discovered(
            payload, node_id="hub", environment_id="host", select_if_new=True
        )
        url = f"/api/v1/conversations/{row.conversation_id}"
        result = client.get(url, params={"last_n_messages": 2})
        assert result.status_code == 200
        assert result.json() == {
            "conversation_id": row.conversation_id,
            "messages": messages[-2:],
            "message_count": 2,
            "has_more": True,
        }
        assert "OLD_HISTORY" not in result.text
        assert client.get(url).json()["transcript_text"] == "OLD_HISTORY flattened"
        assert client.get(url, params={"last_n_messages": 500}).json()["messages"] == messages
        assert not client.get(url, params={"last_n_messages": 500}).json()["has_more"]
        for value in (0, -1, 501, "abc", "1.5"):
            assert client.get(url, params={"last_n_messages": value}).status_code == 422
        assert (
            client.post(
                url + "/refresh",
                params={
                    "last_n_messages": 2,
                    "last_message_only": True,
                },
            ).status_code
            == 422
        )
        # A metadata-only synchronization must preserve the existing transcript boundaries.
        repo.upsert_discovered(
            {**payload, "transcript_messages": None},
            node_id="hub",
            environment_id="host",
            transcript_included=False,
        )
        assert client.get(url, params={"last_n_messages": 1}).json()["messages"] == messages[-1:]
        # Older nodes cannot safely reconstruct message boundaries from prose.
        repo.upsert_discovered(
            {**payload, "transcript_messages": None}, node_id="hub", environment_id="host"
        )
        assert client.get(url, params={"last_n_messages": 1}).status_code == 409
        repo.upsert_discovered(
            {**payload, "transcript_messages": []}, node_id="hub", environment_id="host"
        )
        assert client.get(url, params={"last_n_messages": 1}).json()["messages"] == []
        repo.upsert_discovered(payload, node_id="hub", environment_id="host")
        assert client.delete(url + "/transcript").status_code == 200
        assert "transcript_messages" not in client.get(url).json()["raw_metadata"]
        assert client.get(url, params={"last_n_messages": 1}).status_code == 409
        repo.upsert_discovered(payload, node_id="hub", environment_id="host")
        repo.deselect(row.conversation_id)
        assert client.get(url, params={"last_n_messages": 1}).status_code == 404
        assert "transcript_messages" not in json.loads(
            repo.get(row.conversation_id).raw_metadata_json
        )


def test_codex_message_boundaries_exclude_tools_and_reasoning() -> None:
    record = CodexCatalogAdapter.map_thread(
        {
            "id": "thread-1",
            "turns": [
                {
                    "items": [
                        {"type": "userMessage", "content": [{"type": "text", "text": "hi"}]},
                        {"type": "agentMessage", "text": "hello\nuser: still one message"},
                        {"type": "reasoning", "text": "SECRET"},
                        {"type": "commandExecution", "aggregatedOutput": "SECRET"},
                    ]
                }
            ],
        }
    )
    assert record.transcript_messages == [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "hello\nuser: still one message"},
    ]


@pytest.mark.parametrize("command", ["show", "refresh"])
def test_cli_last_n(command: str) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"messages": []})

    assert (
        run([command, "conv-1", "--last-n-messages", "3"], transport=httpx.MockTransport(handle))
        == 0
    )
    assert requests[0].url.params["last_n_messages"] == "3"


async def test_mcp_last_n_and_connector_tool_schema(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    async def request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"messages": []}

    monkeypatch.setattr(server, "_request", request)
    await server.get_conversation("conv-1", last_n_messages=3, ctx=object())
    await server.refresh_conversation("conv-1", last_n_messages=4, ctx=object())
    assert calls[0]["params"] == {"last_n_messages": 3}
    assert calls[1]["params"]["last_n_messages"] == 4
    # The app connector's tunnel launches this same MCP server: tools/list is its schema source.
    tools = {tool.name: tool.inputSchema for tool in await server.mcp.list_tools()}
    assert "last_n_messages" in tools["get_conversation"]["properties"]
    assert "last_n_messages" in tools["refresh_conversation"]["properties"]
    assert "last_message_only" in tools["refresh_conversation"]["properties"]
