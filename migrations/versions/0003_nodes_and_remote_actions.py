"""Add private hub nodes, environments, and remote action queue.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nodes",
        sa.Column("node_id", sa.String(160), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(80), nullable=False),
        sa.Column("capabilities_json", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("credential_salt", sa.String(128), nullable=False),
        sa.Column("credential_hash", sa.String(128), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("nodes", "platform", "last_seen_at", "heartbeat_expires_at", "updated_at")

    op.create_table(
        "environments",
        sa.Column(
            "node_id",
            sa.String(160),
            sa.ForeignKey("nodes.node_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("environment_id", sa.String(160), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("root_path", sa.Text()),
        sa.Column("sync_policy_json", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("environments", "node_id", "kind", "updated_at")

    op.create_table(
        "node_commands",
        sa.Column("command_id", sa.String(160), primary_key=True),
        sa.Column(
            "node_id",
            sa.String(160),
            sa.ForeignKey("nodes.node_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(80),
            sa.ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
        ),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("claim_token_hash", sa.String(128)),
        sa.Column("result_json", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    _indexes(
        "node_commands",
        "node_id",
        "kind",
        "conversation_id",
        "status",
        "created_at",
        "expires_at",
    )


def downgrade() -> None:
    op.drop_table("node_commands")
    op.drop_table("environments")
    op.drop_table("nodes")


def _indexes(table: str, *columns: str) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])
