"""workspace parts provider secret

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-28
"""
from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Second credential slot. DigiKey needs client_id (kept in
    # parts_provider_api_key) plus client_secret (this column). Mouser
    # leaves it NULL.
    op.add_column(
        "workspaces",
        sa.Column("parts_provider_api_secret", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "parts_provider_api_secret")
