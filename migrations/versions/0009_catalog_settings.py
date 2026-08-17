"""Add persistent catalog preferences.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "catalog_settings" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "catalog_settings",
        sa.Column("key", sa.String(160), primary_key=True),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_catalog_settings_updated_at", "catalog_settings", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_catalog_settings_updated_at", table_name="catalog_settings")
    op.drop_table("catalog_settings")
