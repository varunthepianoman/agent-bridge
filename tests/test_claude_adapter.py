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
    assert rows[0].resume_command == "cd /work/robot && claude --resume session-root"
    child = next(row for row in rows if ":agent:" in row.provider_thread_id)
    assert child.parent_conversation_id is not None
    assert child.resume_command == "cd /work/robot && claude --resume session-root"
