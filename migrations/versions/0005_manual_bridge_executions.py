"""Add authoritative Manual Bridge submissions and executions.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "manual_bridge_messages",
        sa.Column("message_id", sa.String(160), primary_key=True),
        sa.Column("correlation_id", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("destination_kind", sa.String(40), nullable=False),
        sa.Column("destination_id", sa.String(160), nullable=False),
        sa.Column("work_id", sa.String(160)),
        sa.Column("execution_id", sa.String(160)),
        sa.Column("subject", sa.String(320), nullable=False),
        sa.Column("envelope_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("stream", sa.String(160)),
        sa.Column("stream_sequence", sa.BigInteger()),
        sa.Column("duplicate", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes(
        "manual_bridge_messages",
        "correlation_id",
        "kind",
        "destination_kind",
        "destination_id",
        "work_id",
        "execution_id",
        "subject",
        "status",
        "stream",
        "created_at",
        "published_at",
        "updated_at",
    )

    op.create_table(
        "bridge_executions",
        sa.Column("execution_id", sa.String(160), primary_key=True),
        sa.Column(
            "request_message_id",
            sa.String(160),
            sa.ForeignKey("manual_bridge_messages.message_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "cancellation_message_id",
            sa.String(160),
            sa.ForeignKey("manual_bridge_messages.message_id", ondelete="SET NULL"),
        ),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("target_kind", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(160), nullable=False),
        sa.Column("work_id", sa.String(160)),
        sa.Column("conversation_id", sa.String(160)),
        sa.Column("adapter", sa.String(160)),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("result_json", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
    )
    _indexes(
        "bridge_executions",
        "operation",
        "target_kind",
        "target_id",
        "work_id",
        "conversation_id",
        "adapter",
        "status",
        "requested_at",
        "updated_at",
        "completed_at",
        "cancelled_at",
    )

    op.create_table(
        "bridge_execution_attempts",
        sa.Column("attempt_id", sa.String(160), primary_key=True),
        sa.Column(
            "execution_id",
            sa.String(160),
            sa.ForeignKey("bridge_executions.execution_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(160)),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("progress_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("execution_id", "attempt_number", name="uq_bridge_execution_attempt"),
    )
    _indexes(
        "bridge_execution_attempts",
        "execution_id",
        "node_id",
        "status",
        "created_at",
        "started_at",
        "finished_at",
        "updated_at",
    )


def downgrade() -> None:
    op.drop_table("bridge_execution_attempts")
    op.drop_table("bridge_executions")
    op.drop_table("manual_bridge_messages")


def _indexes(table: str, *columns: str) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])
