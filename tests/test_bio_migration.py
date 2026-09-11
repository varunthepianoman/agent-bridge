from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def test_bio_migration_preserves_conversations_and_fts_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("AGENT_BRIDGE_DATABASE_URL", database_url)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0013")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """INSERT INTO conversations
                (conversation_id, provider, provider_thread_id, node_id, environment_id,
                 title, preview, transcript_text, status, last_synced_at, pinned, hidden,
                 archived, notes, tags_json, raw_metadata_json)
                VALUES ('conversation-1', 'codex', 'thread-1', 'hub', 'host', 'Socket work',
                 'preview', 'assistant: result', 'idle', CURRENT_TIMESTAMP, 0, 0, 0,
                 'private operational note', '[\"network\"]', '{}')"""
            )
        )
        connection.execute(
            sa.text(
                """INSERT INTO conversation_fts
                (conversation_id, title, preview, transcript_text, notes, tags)
                VALUES ('conversation-1', 'Socket work', 'preview', 'assistant: result',
                 'private operational note', 'network')"""
            )
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT bio, notes FROM conversations WHERE conversation_id='conversation-1'")
        ).one()
        assert row == ("", "private operational note")
        assert connection.execute(
            sa.text("SELECT COUNT(*) FROM conversation_fts WHERE conversation_fts MATCH 'network'")
        ).scalar_one() == 1
        columns = connection.execute(sa.text("PRAGMA table_info(conversation_fts)")).all()
        assert "bio" in {column[1] for column in columns}
