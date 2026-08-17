"""Replace organizational surfaces with the conversation core.

Revision ID: 0008
Revises: 0007
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_TABLES = (
    "collaboration_messages",
    "collaboration_rooms",
    "registered_endpoints",
    "role_rollup_states",
    "coordinator_activations",
    "coordinator_intake_events",
    "coordinator_intakes",
    "bridge_execution_attempts",
    "bridge_executions",
    "manual_bridge_messages",
    "role_reports",
    "role_events",
    "role_checkpoints",
    "role_conversations",
    "role_leases",
    "relationships",
    "coordinator_roles",
    "work_items",
)

# Database.initialize() in early single-user-core builds could create these tables before
# Alembic advanced past 0007. They carried no supported data at that revision, so 0008 can
# safely remove empty instances and recreate them with migration-owned constraints/indexes.
PRECREATED_CORE_TABLES = (
    "collection_members",
    "room_members",
    "conversation_messages",
    "attention_items",
    "nats_events",
    "legacy_exports",
    "collections",
    "rooms",
)


def _remove_empty_precreated_core_tables() -> None:
    connection = op.get_bind()
    present = set(sa.inspect(connection).get_table_names())
    for table in PRECREATED_CORE_TABLES:
        if table not in present:
            continue
        count = connection.execute(sa.text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
        if count:
            raise RuntimeError(
                f"cannot migrate hybrid 0007 database: precreated core table {table} "
                f"contains {count} row(s)"
            )
        op.drop_table(table)


def upgrade() -> None:
    _remove_empty_precreated_core_tables()
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("conversation_number", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("alias", sa.Text(), nullable=False, server_default="Untitled conversation")
        )
        batch.add_column(
            sa.Column("alias_updated_by", sa.String(40), nullable=False, server_default="provider")
        )
        batch.add_column(sa.Column("alias_updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("conversation_kind", sa.String(40), nullable=False, server_default="full")
        )
        batch.add_column(
            sa.Column("delivery_mode", sa.String(40), nullable=False, server_default="direct")
        )
        batch.add_column(
            sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.create_unique_constraint("uq_conversation_number", ["conversation_number"])
        batch.create_index("ix_conversations_conversation_number", ["conversation_number"])
        batch.create_index("ix_conversations_conversation_kind", ["conversation_kind"])
        batch.create_index("ix_conversations_delivery_mode", ["delivery_mode"])
        batch.create_index("ix_conversations_selected", ["selected"])

    op.execute(
        "UPDATE conversations SET alias = "
        "COALESCE(NULLIF(title, ''), provider_title, 'Untitled conversation')"
    )
    op.execute(
        "UPDATE conversations SET conversation_kind = 'native_subagent' "
        "WHERE source LIKE 'subAgent%'"
    )
    op.execute(
        "UPDATE conversations SET delivery_mode = 'catalog_only' "
        "WHERE conversation_kind = 'native_subagent'"
    )

    op.create_table(
        "collections",
        sa.Column("collection_id", sa.String(160), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("filter_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_collections_kind", "collections", ["kind"])
    op.create_index("ix_collections_updated_at", "collections", ["updated_at"])
    op.create_table(
        "collection_members",
        sa.Column(
            "collection_id",
            sa.String(160),
            sa.ForeignKey("collections.collection_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "conversation_id",
            sa.String(80),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "rooms",
        sa.Column("room_id", sa.String(160), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "room_members",
        sa.Column(
            "room_id",
            sa.String(160),
            sa.ForeignKey("rooms.room_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "conversation_id",
            sa.String(80),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("delivery_mode", sa.String(40), nullable=False, server_default="wake"),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "conversation_messages",
        sa.Column("message_id", sa.String(160), primary_key=True),
        sa.Column("correlation_id", sa.String(160), nullable=False),
        sa.Column("causation_id", sa.String(160)),
        sa.Column("source_conversation_id", sa.String(80)),
        sa.Column("target_conversation_id", sa.String(80)),
        sa.Column("room_id", sa.String(160)),
        sa.Column("actor_kind", sa.String(40), nullable=False),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("subject", sa.String(320)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in (
        "correlation_id",
        "causation_id",
        "source_conversation_id",
        "target_conversation_id",
        "room_id",
        "actor_kind",
        "operation",
        "state",
        "subject",
        "created_at",
        "updated_at",
    ):
        op.create_index(f"ix_conversation_messages_{column}", "conversation_messages", [column])
    op.create_table(
        "attention_items",
        sa.Column("attention_id", sa.String(160), primary_key=True),
        sa.Column("conversation_id", sa.String(80)),
        sa.Column("correlation_id", sa.String(160)),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
    )
    for column in (
        "conversation_id",
        "correlation_id",
        "category",
        "kind",
        "acknowledged",
        "created_at",
    ):
        op.create_index(f"ix_attention_items_{column}", "attention_items", [column])
    op.create_table(
        "nats_events",
        sa.Column("event_id", sa.String(160), primary_key=True),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("direction", sa.String(20)),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(320)),
        sa.Column("message_id", sa.String(160)),
        sa.Column("correlation_id", sa.String(160)),
        sa.Column("node_id", sa.String(160)),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in (
        "category",
        "direction",
        "severity",
        "subject",
        "message_id",
        "correlation_id",
        "node_id",
        "occurred_at",
    ):
        op.create_index(f"ix_nats_events_{column}", "nats_events", [column])

    op.create_table(
        "legacy_exports",
        sa.Column("export_id", sa.String(160), primary_key=True),
        sa.Column("source_revision", sa.String(40), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_legacy_exports_created_at", "legacy_exports", ["created_at"])
    connection = op.get_bind()
    snapshot: dict[str, list[dict[str, object]]] = {}
    for table in LEGACY_TABLES:
        rows = connection.execute(sa.text(f'SELECT * FROM "{table}"')).mappings().all()
        snapshot[table] = [dict(row) for row in rows]
    connection.execute(
        sa.text(
            "INSERT INTO legacy_exports "
            "(export_id, source_revision, data_json, created_at) "
            "VALUES (:export_id, :source_revision, :data_json, :created_at)"
        ),
        {
            "export_id": f"legacy-{uuid4().hex}",
            "source_revision": "0007",
            "data_json": json.dumps(snapshot, default=str, separators=(",", ":")),
            "created_at": datetime.now(UTC),
        },
    )
    for table in LEGACY_TABLES:
        op.drop_table(table)


def downgrade() -> None:
    raise RuntimeError(
        "0008 is an intentional product-boundary migration; restore a pre-0008 backup "
        "or use legacy_exports instead of downgrading in place"
    )
