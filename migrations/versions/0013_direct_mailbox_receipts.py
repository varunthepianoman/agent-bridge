"""Add direct mailbox acknowledgment receipts.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "conversation_messages" in tables:
        columns = {column["name"] for column in inspector.get_columns("conversation_messages")}
        if "acknowledgement_requested" not in columns:
            op.add_column(
                "conversation_messages",
                sa.Column(
                    "acknowledgement_requested",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                ),
            )

    if "mailbox_deliveries" in tables:
        columns = {column["name"] for column in inspector.get_columns("mailbox_deliveries")}
        additions = (
            sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("acknowledgement_detail", sa.Text(), nullable=True),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "acknowledgement_attention_emitted_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "terminal_attention_emitted_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        for column in additions:
            if column.name not in columns:
                op.add_column("mailbox_deliveries", column)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "mailbox_deliveries" in tables:
        columns = {column["name"] for column in inspector.get_columns("mailbox_deliveries")}
        with op.batch_alter_table("mailbox_deliveries") as batch:
            for name in (
                "terminal_attention_emitted_at",
                "acknowledgement_attention_emitted_at",
                "revision",
                "attempt",
                "acknowledgement_detail",
                "acknowledged_at",
            ):
                if name in columns:
                    batch.drop_column(name)
    if "conversation_messages" in tables:
        columns = {column["name"] for column in inspector.get_columns("conversation_messages")}
        if "acknowledgement_requested" in columns:
            with op.batch_alter_table("conversation_messages") as batch:
                batch.drop_column("acknowledgement_requested")
