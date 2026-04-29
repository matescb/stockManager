"""workspace scanner backend + license key

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-30
"""
from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Which client-side decoder runs in the scanner. 'zxing' is the
    # royalty-free open-source default; 'scandit' is opt-in and requires a
    # workspace-scoped license key (next column).
    op.add_column(
        "workspaces",
        sa.Column(
            "scanner",
            sa.String(length=40),
            nullable=False,
            server_default="zxing",
        ),
    )
    # Scandit license blob. ~840 chars today; 2048 leaves room.
    op.add_column(
        "workspaces",
        sa.Column("scanner_license_key", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "scanner_license_key")
    op.drop_column("workspaces", "scanner")
