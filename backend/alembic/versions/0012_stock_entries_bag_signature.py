"""stock_entries.bag_signature for re-scan recognition

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-30

When a bag is scanned via /parts/scan-import, we hash the raw bag code
(sha256 hex of the normalised payload) and store it on the resulting
stock_entry. Subsequent scans of the same physical bag get recognised
and offered an inline "remove qty from this lot" affordance instead of
silently double-importing.
"""
from alembic import op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stock_entries",
        sa.Column("bag_signature", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_stock_ws_bag_signature",
        "stock_entries",
        ["workspace_id", "bag_signature"],
    )


def downgrade() -> None:
    op.drop_index("ix_stock_ws_bag_signature", table_name="stock_entries")
    op.drop_column("stock_entries", "bag_signature")
