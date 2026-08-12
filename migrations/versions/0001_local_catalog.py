"""Create the local Codex conversation catalog.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.String(length=80), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_thread_id", sa.String(length=160), nullable=False),
        sa.Column("node_id", sa.String(length=160), nullable=False),
        sa.Column("environment_id", sa.String(length=160), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("provider_title", sa.Text(), nullable=True),
        sa.Column("preview", sa.Text(), nullable=False),
        sa.Column("transcript_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=True),
        sa.Column("cwd", sa.Text(), nullable=True),
        sa.Column("repository", sa.Text(), nullable=True),
        sa.Column("branch", sa.Text(), nullable=True),
        sa.Column("commit_hash", sa.String(length=80), nullable=True),
        sa.Column("parent_provider_thread_id", sa.String(length=160), nullable=True),
        sa.Column("parent_conversation_id", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("tags_json", sa.Text(), nullable=False),
        sa.Column("resume_command", sa.Text(), nullable=True),
        sa.Column("raw_metadata_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("conversation_id"),
        sa.UniqueConstraint(
            "provider",
            "provider_thread_id",
            "node_id",
            "environment_id",
            name="uq_provider_thread",
        ),
    )
    for column in (
        "provider",
        "provider_thread_id",
        "node_id",
        "environment_id",
        "status",
        "parent_conversation_id",
        "last_activity_at",
        "pinned",
        "hidden",
        "archived",
    ):
        op.create_index(f"ix_conversations_{column}", "conversations", [column])
    op.execute(
        """CREATE VIRTUAL TABLE conversation_fts USING fts5(
        conversation_id UNINDEXED, title, preview, transcript_text, notes, tags
        )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS conversation_fts")
    op.drop_table("conversations")
