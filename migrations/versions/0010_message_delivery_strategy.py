"""Add message delivery strategy and route observability.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns("conversation_messages")
    }
    if "delivery_strategy" not in columns:
        op.add_column(
            "conversation_messages",
            sa.Column(
                "delivery_strategy",
                sa.String(40),
                nullable=False,
                server_default="queue",
            ),
        )
        op.create_index(
            "ix_conversation_messages_delivery_strategy",
            "conversation_messages",
            ["delivery_strategy"],
        )
    if "delivery_route" not in columns:
        op.add_column(
            "conversation_messages",
            sa.Column("delivery_route", sa.String(40), nullable=True),
        )
        op.create_index(
            "ix_conversation_messages_delivery_route",
            "conversation_messages",
            ["delivery_route"],
        )


def downgrade() -> None:
    columns = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns("conversation_messages")
    }
    if "delivery_route" in columns:
        op.drop_index(
            "ix_conversation_messages_delivery_route",
            table_name="conversation_messages",
        )
        op.drop_column("conversation_messages", "delivery_route")
    if "delivery_strategy" in columns:
        op.drop_index(
            "ix_conversation_messages_delivery_strategy",
            table_name="conversation_messages",
        )
        op.drop_column("conversation_messages", "delivery_strategy")
