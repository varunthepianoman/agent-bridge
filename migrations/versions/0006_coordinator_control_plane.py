"""Add coordinator intake, activation, authority, and rollup state.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coordinator_intakes",
        sa.Column("request_id", sa.String(160), primary_key=True),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("routed_work_id", sa.String(160)),
        sa.Column(
            "routed_role_id",
            sa.String(160),
            sa.ForeignKey("coordinator_roles.role_id", ondelete="SET NULL"),
        ),
        sa.Column("proposed_actions_json", sa.Text(), nullable=False),
        sa.Column("proposed_topology_json", sa.Text(), nullable=False),
        sa.Column("attention_required", sa.Text()),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("executed", sa.Boolean(), nullable=False),
        sa.Column("decision_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _indexes(
        "coordinator_intakes",
        "mode",
        "status",
        "routed_work_id",
        "routed_role_id",
        "approval_required",
        "executed",
        "created_at",
        "updated_at",
    )

    op.create_table(
        "coordinator_intake_events",
        sa.Column("event_id", sa.String(160), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(160),
            sa.ForeignKey("coordinator_intakes.request_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(80), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", "sequence", name="uq_coordinator_intake_event"),
    )
    _indexes("coordinator_intake_events", "request_id", "type", "occurred_at")

    op.create_table(
        "coordinator_activations",
        sa.Column("activation_id", sa.String(160), primary_key=True),
        sa.Column(
            "role_id",
            sa.String(160),
            sa.ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "intake_request_id",
            sa.String(160),
            sa.ForeignKey("coordinator_intakes.request_id", ondelete="SET NULL"),
        ),
        sa.Column("holder_id", sa.String(160), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("checkpoint_version_before", sa.Integer(), nullable=False),
        sa.Column("checkpoint_version_after", sa.Integer()),
        sa.Column("conversation_id", sa.String(160)),
        sa.Column("authority_json", sa.Text(), nullable=False),
        sa.Column("usage_json", sa.Text(), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
    )
    _indexes(
        "coordinator_activations",
        "role_id",
        "intake_request_id",
        "holder_id",
        "fencing_token",
        "status",
        "conversation_id",
        "started_at",
        "updated_at",
        "completed_at",
    )

    op.create_table(
        "role_rollup_states",
        sa.Column("rollup_id", sa.String(321), primary_key=True),
        sa.Column(
            "parent_role_id",
            sa.String(160),
            sa.ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "child_role_id",
            sa.String(160),
            sa.ForeignKey("coordinator_roles.role_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("incorporated_checkpoint_version", sa.Integer(), nullable=False),
        sa.Column(
            "report_id",
            sa.String(160),
            sa.ForeignKey("role_reports.report_id", ondelete="SET NULL"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("parent_role_id", "child_role_id", name="uq_role_rollup_parent_child"),
    )
    _indexes("role_rollup_states", "parent_role_id", "child_role_id", "report_id", "updated_at")


def downgrade() -> None:
    op.drop_table("role_rollup_states")
    op.drop_table("coordinator_activations")
    op.drop_table("coordinator_intake_events")
    op.drop_table("coordinator_intakes")


def _indexes(table: str, *columns: str) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column])
