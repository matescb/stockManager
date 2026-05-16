"""Add updated_by audit column to sourcing alerts.

Revision ID: 0066
Revises: 0065
Create Date: 2026-05-16

AUD-127 / issue #826.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sourcing_alerts",
        sa.Column("updated_by", sa.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_sourcing_alerts_updated_by_users",
        "sourcing_alerts",
        "users",
        ["updated_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_sourcing_alerts_updated_by_users",
        "sourcing_alerts",
        type_="foreignkey",
    )
    op.drop_column("sourcing_alerts", "updated_by")
