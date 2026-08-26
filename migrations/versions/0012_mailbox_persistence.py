"""Add per-recipient mailbox persistence and fenced listeners.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "room_members" in existing:
        op.execute(
            sa.text(
                "UPDATE room_members SET delivery_mode = 'mailbox' "
                "WHERE delivery_mode = 'wake'"
            )
        )
    if "mailbox_deliveries" not in existing:
        op.create_table(
            "mailbox_deliveries",
            sa.Column("message_id", sa.String(160), nullable=False),
            sa.Column("recipient_conversation_id", sa.String(80), nullable=False),
            sa.Column("state", sa.String(40), nullable=False, server_default="pending"),
            sa.Column("listener_id", sa.String(160), nullable=True),
            sa.Column("fencing_token", sa.BigInteger(), nullable=True),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("reply_message_id", sa.String(160), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("attention_emitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["message_id"], ["conversation_messages.message_id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["recipient_conversation_id"],
                ["conversations.conversation_id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("message_id", "recipient_conversation_id"),
        )
        for column in (
            "recipient_conversation_id",
            "state",
            "listener_id",
            "reply_message_id",
            "created_at",
            "updated_at",
            "received_at",
            "completed_at",
            "attention_emitted_at",
        ):
            op.create_index(f"ix_mailbox_deliveries_{column}", "mailbox_deliveries", [column])

    if "mailbox_events" not in existing:
        op.create_table(
            "mailbox_events",
            sa.Column("event_id", sa.String(160), primary_key=True),
            sa.Column("message_id", sa.String(160), nullable=False),
            sa.Column("recipient_conversation_id", sa.String(80), nullable=False),
            sa.Column("event_kind", sa.String(40), nullable=False),
            sa.Column("from_state", sa.String(40), nullable=True),
            sa.Column("to_state", sa.String(40), nullable=False),
            sa.Column("listener_id", sa.String(160), nullable=True),
            sa.Column("fencing_token", sa.BigInteger(), nullable=True),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["message_id", "recipient_conversation_id"],
                ["mailbox_deliveries.message_id", "mailbox_deliveries.recipient_conversation_id"],
                ondelete="CASCADE",
            ),
        )
        for column in (
            "message_id",
            "recipient_conversation_id",
            "event_kind",
            "to_state",
            "listener_id",
            "created_at",
        ):
            op.create_index(f"ix_mailbox_events_{column}", "mailbox_events", [column])

    if "mailbox_listeners" not in existing:
        op.create_table(
            "mailbox_listeners",
            sa.Column("conversation_id", sa.String(80), primary_key=True),
            sa.Column("listener_id", sa.String(160), nullable=False),
            sa.Column("fencing_token", sa.BigInteger(), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("stop_requested_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["conversation_id"], ["conversations.conversation_id"], ondelete="CASCADE"
            ),
            sa.UniqueConstraint("listener_id"),
        )
        for column in ("listener_id", "heartbeat_at", "expires_at", "stop_requested_at"):
            op.create_index(f"ix_mailbox_listeners_{column}", "mailbox_listeners", [column])


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table_name in ("mailbox_events", "mailbox_deliveries", "mailbox_listeners"):
        if table_name in existing:
            op.drop_table(table_name)
    if "room_members" in existing:
        op.execute(
            sa.text(
                "UPDATE room_members SET delivery_mode = 'wake' "
                "WHERE delivery_mode = 'mailbox'"
            )
        )
