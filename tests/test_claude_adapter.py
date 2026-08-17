from pathlib import Path

import pytest

from agent_bridge_catalog.db import Database
from agent_bridge_catalog.repository import CatalogRepository
from agent_bridge_catalog.sync import CatalogSynchronizer
from agent_bridge_providers.claude import ClaudeCatalogAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "claude"


@pytest.mark.asyncio
async def test_discovers_root_and_native_subagent_without_tool_content() -> None:
    adapter = ClaudeCatalogAdapter(FIXTURE)
    records = [item async for item in adapter.discover(include_turns=True)]

    assert [item.provider_thread_id for item in records] == [
        "session-root",
        "session-root:agent:review",
    ]
    root, child = records
    assert root.provider == "claude"
    assert root.title == "Investigate reconnect behavior"
    assert root.cwd == "/work/robot"
    assert root.git_branch == "feature/reconnect"
    assert "Find the reconnect bug" in root.transcript_text
    assert "session generation is stale" in root.transcript_text
    assert "SECRET_TOOL_OUTPUT" not in root.transcript_text
    assert child.parent_thread_id == "session-root"
    assert child.source_kind == "subAgent"
    assert child.title == "Audit the plan"
    assert "SECRET_REASONING" not in child.transcript_text


@pytest.mark.asyncio
async def test_reconciles_and_generates_provider_native_resume_command(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'catalog.db'}")
    database.initialize()
    repository = CatalogRepository(database)
    synchronizer = CatalogSynchronizer(
        repository,
        ClaudeCatalogAdapter(FIXTURE),
        node_id="desktop",
        environment_id="host",
    )

    result = await synchronizer.reconcile(include_turns=False)
    rows, total = repository.list(provider="claude", selected_only=False)

    assert result.discovered == result.imported == total == 2
    assert rows[0].resume_command == (
        "cd /work/robot && claude --dangerously-skip-permissions --resume session-root"
    )
    child = next(row for row in rows if ":agent:" in row.provider_thread_id)
    assert child.parent_conversation_id is not None
    assert child.resume_command == (
        "cd /work/robot && claude --dangerously-skip-permissions --resume session-root"
    )


@pytest.mark.asyncio
async def test_prefers_claude_generated_ai_title_over_first_prompt(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "-work-robot"
    project.mkdir(parents=True)
    (project / "session.jsonl").write_text(
        "\n".join(
            (
                '{"type":"user","sessionId":"session","cwd":"/work/robot",'
                '"timestamp":"2026-01-01T00:00:00Z","message":{"role":"user",'
                '"content":"A very long first prompt that should not be used as the title"}}',
                '{"type":"ai-title","sessionId":"session","aiTitle":"Investigate robot reconnect"}',
            )
        ),
        encoding="utf-8",
    )

    records = [item async for item in ClaudeCatalogAdapter(tmp_path).discover()]

    assert records[0].title == "Investigate robot reconnect"


@pytest.mark.asyncio
async def test_prefers_native_subagent_description_over_first_prompt(tmp_path: Path) -> None:
    root = tmp_path / "projects" / "-work-robot" / "session"
    subagents = root / "subagents"
    subagents.mkdir(parents=True)
    (tmp_path / "projects" / "-work-robot" / "session.jsonl").write_text(
        '{"type":"user","sessionId":"session","cwd":"/work/robot",'
        '"timestamp":"2026-01-01T00:00:00Z","message":{"role":"user",'
        '"content":"Root task"}}\n',
        encoding="utf-8",
    )
    child = subagents / "agent-review.jsonl"
    child.write_text(
        '{"type":"user","sessionId":"session","agentId":"review",'
        '"cwd":"/work/robot","timestamp":"2026-01-01T00:01:00Z",'
        '"message":{"role":"user","content":"A long implementation prompt"}}\n',
        encoding="utf-8",
    )
    child.with_suffix(".meta.json").write_text(
        '{"description":"Review reconnect implementation"}', encoding="utf-8"
    )

    records = [item async for item in ClaudeCatalogAdapter(tmp_path).discover()]

    assert records[1].title == "Review reconnect implementation"
