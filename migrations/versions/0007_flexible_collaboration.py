"""Add registered endpoints, durable rooms, and collaboration messages.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "registered_endpoints",
        sa.Column("endpoint_id", sa.String(160), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("address_kind", sa.String(40), nullable=False),
        sa.Column("address_id", sa.String(160), nullable=False),
        sa.Column("capabilities_json", sa.Text(), nullable=False),
        sa.Column("work_ids_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("extensions_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("registered_endpoints", "address_kind", "address_id", "status", "updated_at")
    op.create_table(
        "collaboration_rooms",
        sa.Column("room_id", sa.String(160), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("work_id", sa.String(160)),
        sa.Column("durable", sa.Boolean(), nullable=False),
        sa.Column("members_json", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("extensions_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes("collaboration_rooms", "work_id", "updated_at")
    op.create_table(
        "collaboration_messages",
        sa.Column("collaboration_id", sa.String(160), primary_key=True),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("sender_kind", sa.String(40), nullable=False),
        sa.Column("sender_id", sa.String(160), nullable=False),
        sa.Column("destinations_json", sa.Text(), nullable=False),
        sa.Column("body_json", sa.Text(), nullable=False),
        sa.Column("work_id", sa.String(160)),
        sa.Column("correlation_id", sa.String(160), nullable=False),
        sa.Column("causation_id", sa.String(160)),
        sa.Column("reply_to_json", sa.Text()),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("bridge_message_ids_json", sa.Text(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("extensions_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes(
        "collaboration_messages",
        "operation",
        "sender_kind",
        "sender_id",
        "work_id",
        "correlation_id",
        "causation_id",
        "state",
        "created_at",
        "updated_at",
    )


def downgrade() -> None:
    op.drop_table("collaboration_messages")
    op.drop_table("collaboration_rooms")
    op.drop_table("registered_endpoints")


def _indexes(table: str, *columns: str) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])
