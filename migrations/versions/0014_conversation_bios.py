"""Add public directory bios to conversations and search.

Revision ID: 0014
Revises: 0013
"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fts_rows(connection: Any, *, include_bio: bool) -> list[dict[str, str]]:
    ids = {
        str(row[0])
        for row in connection.execute(sa.text("SELECT conversation_id FROM conversation_fts"))
    }
    if not ids:
        return []
    rows = connection.execute(sa.text("SELECT * FROM conversations")).mappings()
    result: list[dict[str, str]] = []
    for row in rows:
        conversation_id = str(row["conversation_id"])
        if conversation_id not in ids:
            continue
        tags = json.loads(str(row["tags_json"] or "[]"))
        values = {
            "conversation_id": conversation_id,
            "title": " ".join(
                filter(None, (row["alias"], row["provider_title"], row["title"]))
            ),
            "preview": str(row["preview"] or ""),
            "transcript_text": str(row["transcript_text"] or ""),
            "notes": str(row["notes"] or ""),
            "tags": " ".join(str(tag) for tag in tags) if isinstance(tags, list) else "",
        }
        if include_bio:
            values["bio"] = str(row["bio"] or "")
        result.append(values)
    return result


def _rebuild_fts(connection: Any, *, include_bio: bool) -> None:
    rows = _fts_rows(connection, include_bio=include_bio)
    op.execute("DROP TABLE conversation_fts")
    columns = "conversation_id UNINDEXED, title, preview, transcript_text"
    columns += ", bio" if include_bio else ""
    op.execute(f"CREATE VIRTUAL TABLE conversation_fts USING fts5({columns}, notes, tags)")
    if rows:
        names = "conversation_id, title, preview, transcript_text"
        names += ", bio" if include_bio else ""
        names += ", notes, tags"
        placeholders = ", ".join(f":{name.strip()}" for name in names.split(","))
        connection.execute(
            sa.text(f"INSERT INTO conversation_fts ({names}) VALUES ({placeholders})"), rows
        )


def upgrade() -> None:
    connection = op.get_bind()
    columns = {column["name"] for column in sa.inspect(connection).get_columns("conversations")}
    if "bio" not in columns:
        op.add_column(
            "conversations",
            sa.Column("bio", sa.Text(), nullable=False, server_default=""),
        )
    _rebuild_fts(connection, include_bio=True)


def downgrade() -> None:
    connection = op.get_bind()
    _rebuild_fts(connection, include_bio=False)
    columns = {column["name"] for column in sa.inspect(connection).get_columns("conversations")}
    if "bio" in columns:
        with op.batch_alter_table("conversations") as batch:
            batch.drop_column("bio")
