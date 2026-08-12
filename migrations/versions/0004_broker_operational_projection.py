"""Add the queryable broker operational projection.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broker_messages",
        sa.Column("message_id", sa.String(160), primary_key=True),
        sa.Column("subject", sa.String(320), nullable=False),
        sa.Column("stream", sa.String(160)),
        sa.Column("stream_sequence", sa.BigInteger()),
        sa.Column("message_type", sa.String(80), nullable=False),
        sa.Column("source_kind", sa.String(40)),
        sa.Column("source_id", sa.String(160)),
        sa.Column("destination_kind", sa.String(40)),
        sa.Column("destination_id", sa.String(160)),
        sa.Column("correlation_id", sa.String(160)),
        sa.Column("causation_id", sa.String(160)),
        sa.Column("work_id", sa.String(160)),
        sa.Column("role_id", sa.String(160)),
        sa.Column("execution_id", sa.String(160)),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("payload_summary_json", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes(
        "broker_messages",
        "subject",
        "stream",
        "stream_sequence",
        "message_type",
        "source_kind",
        "source_id",
        "destination_kind",
        "destination_id",
        "correlation_id",
        "causation_id",
        "work_id",
        "role_id",
        "execution_id",
        "state",
        "sent_at",
        "expires_at",
        "first_observed_at",
        "last_observed_at",
    )

    op.create_table(
        "broker_deliveries",
        sa.Column("delivery_id", sa.String(240), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(160),
            sa.ForeignKey("broker_messages.message_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stream", sa.String(160), nullable=False),
        sa.Column("consumer", sa.String(160), nullable=False),
        sa.Column("delivery_sequence", sa.BigInteger(), nullable=False),
        sa.Column("redelivery_count", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("node_id", sa.String(160)),
        sa.Column("error_json", sa.Text()),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ack_deadline_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "message_id", "consumer", "delivery_sequence", name="uq_broker_delivery_attempt"
        ),
    )
    _indexes(
        "broker_deliveries",
        "message_id",
        "stream",
        "consumer",
        "state",
        "node_id",
        "delivered_at",
        "ack_deadline_at",
        "acknowledged_at",
        "last_observed_at",
    )

    op.create_table(
        "broker_dead_letters",
        sa.Column("dead_letter_id", sa.String(240), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(160),
            sa.ForeignKey("broker_messages.message_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stream", sa.String(160), nullable=False),
        sa.Column("consumer", sa.String(160), nullable=False),
        sa.Column("reason", sa.String(160), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "message_id", "consumer", name="uq_broker_dead_letter_message_consumer"
        ),
    )
    _indexes(
        "broker_dead_letters",
        "message_id",
        "stream",
        "consumer",
        "reason",
        "dead_lettered_at",
        "last_observed_at",
        "resolved_at",
    )

    op.create_table(
        "broker_consumer_states",
        sa.Column("consumer_key", sa.String(321), primary_key=True),
        sa.Column("stream", sa.String(160), nullable=False),
        sa.Column("consumer", sa.String(160), nullable=False),
        sa.Column("pending_count", sa.BigInteger(), nullable=False),
        sa.Column("ack_pending_count", sa.BigInteger(), nullable=False),
        sa.Column("redelivered_count", sa.BigInteger(), nullable=False),
        sa.Column("delivered_stream_sequence", sa.BigInteger(), nullable=False),
        sa.Column("ack_floor_stream_sequence", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("stream", "consumer", name="uq_broker_stream_consumer"),
    )
    _indexes("broker_consumer_states", "stream", "consumer", "state", "observed_at")


def downgrade() -> None:
    op.drop_table("broker_consumer_states")
    op.drop_table("broker_dead_letters")
    op.drop_table("broker_deliveries")
    op.drop_table("broker_messages")


def _indexes(table: str, *columns: str) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])
