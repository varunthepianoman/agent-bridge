"""Add work organization and durable logical roles.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_items",
        sa.Column("work_id", sa.String(160), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text()),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("repository_id", sa.String(320)),
        sa.Column("branch", sa.Text()),
        sa.Column("pull_request", sa.String(160)),
        sa.Column("tags_json", sa.Text(), nullable=False),
        sa.Column("extensions_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("work_items", "status", "repository_id", "pull_request", "updated_at")

    op.create_table(
        "relationships",
        sa.Column("relationship_id", sa.String(160), primary_key=True),
        sa.Column("source_kind", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(160), nullable=False),
        sa.Column("target_kind", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(160), nullable=False),
        sa.Column("type", sa.String(120), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("extensions_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes(
        "relationships",
        "source_kind",
        "source_id",
        "target_kind",
        "target_id",
        "type",
        "created_at",
    )

    op.create_table(
        "coordinator_roles",
        sa.Column("role_id", sa.String(160), primary_key=True),
        sa.Column("role_type", sa.String(80), nullable=False),
        sa.Column("scope", sa.String(320), nullable=False),
        sa.Column("charter", sa.Text(), nullable=False),
        sa.Column("authority_profile", sa.String(160), nullable=False),
        sa.Column("autonomy_mode", sa.String(40), nullable=False),
        sa.Column(
            "parent_role_id",
            sa.String(160),
            sa.ForeignKey("coordinator_roles.role_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "current_conversation_id",
            sa.String(80),
            sa.ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
        ),
        sa.Column("checkpoint_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("extensions_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes(
        "coordinator_roles",
        "role_type",
        "scope",
        "parent_role_id",
        "current_conversation_id",
        "status",
        "updated_at",
    )

    op.create_table(
        "role_conversations",
        sa.Column("role_conversation_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "role_id",
            sa.String(160),
            sa.ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.String(80),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detached_at", sa.DateTime(timezone=True)),
        sa.Column("handoff_summary", sa.Text()),
        sa.UniqueConstraint("role_id", "conversation_id", name="uq_role_conversation"),
    )
    _indexes("role_conversations", "role_id", "conversation_id")

    op.create_table(
        "role_checkpoints",
        sa.Column(
            "role_id",
            sa.String(160),
            sa.ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_role_checkpoints_created_at", "role_checkpoints", ["created_at"])

    op.create_table(
        "role_events",
        sa.Column("event_id", sa.String(160), primary_key=True),
        sa.Column(
            "role_id",
            sa.String(160),
            sa.ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(120), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("role_id", "sequence", name="uq_role_event_sequence"),
    )
    _indexes("role_events", "role_id", "type", "occurred_at")

    op.create_table(
        "role_reports",
        sa.Column("report_id", sa.String(160), primary_key=True),
        sa.Column(
            "reporting_role_id",
            sa.String(160),
            sa.ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipient_role_id",
            sa.String(160),
            sa.ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("checkpoint_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("role_reports", "reporting_role_id", "recipient_role_id", "created_at")

    op.create_table(
        "role_leases",
        sa.Column(
            "role_id",
            sa.String(160),
            sa.ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("holder_id", sa.String(160), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("role_leases", "holder_id", "expires_at")


def downgrade() -> None:
    for table in (
        "role_leases",
        "role_reports",
        "role_events",
        "role_checkpoints",
        "role_conversations",
        "coordinator_roles",
        "relationships",
        "work_items",
    ):
        op.drop_table(table)


def _indexes(table: str, *columns: str) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])
