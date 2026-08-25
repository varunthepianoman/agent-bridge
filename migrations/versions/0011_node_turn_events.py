"""Add idempotent remote node turn events.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "node_turn_events" in inspector.get_table_names():
        return
    op.create_table(
        "node_turn_events",
        sa.Column("event_id", sa.String(500), primary_key=True),
        sa.Column("node_id", sa.String(160), nullable=False),
        sa.Column("environment_id", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_thread_id", sa.String(500), nullable=False),
        sa.Column("provider_turn_id", sa.String(500), nullable=False),
        sa.Column("command_id", sa.String(160), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.node_id"]),
        sa.ForeignKeyConstraint(["command_id"], ["node_commands.command_id"]),
    )
    op.create_index("ix_node_turn_events_node_id", "node_turn_events", ["node_id"])
    op.create_index(
        "ix_node_turn_events_environment_id", "node_turn_events", ["environment_id"]
    )
    op.create_index("ix_node_turn_events_provider", "node_turn_events", ["provider"])
    op.create_index(
        "ix_node_turn_events_provider_thread_id",
        "node_turn_events",
        ["provider_thread_id"],
    )
    op.create_index(
        "ix_node_turn_events_provider_turn_id", "node_turn_events", ["provider_turn_id"]
    )
    op.create_index("ix_node_turn_events_command_id", "node_turn_events", ["command_id"])
    op.create_index("ix_node_turn_events_status", "node_turn_events", ["status"])
    op.create_index("ix_node_turn_events_created_at", "node_turn_events", ["created_at"])


def downgrade() -> None:
    if "node_turn_events" not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("ix_node_turn_events_created_at", table_name="node_turn_events")
    op.drop_index("ix_node_turn_events_status", table_name="node_turn_events")
    op.drop_index("ix_node_turn_events_command_id", table_name="node_turn_events")
    op.drop_index("ix_node_turn_events_provider_turn_id", table_name="node_turn_events")
    op.drop_index("ix_node_turn_events_provider_thread_id", table_name="node_turn_events")
    op.drop_index("ix_node_turn_events_provider", table_name="node_turn_events")
    op.drop_index("ix_node_turn_events_environment_id", table_name="node_turn_events")
    op.drop_index("ix_node_turn_events_node_id", table_name="node_turn_events")
    op.drop_table("node_turn_events")
