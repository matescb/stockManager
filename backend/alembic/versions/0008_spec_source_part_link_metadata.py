"""spec source + part link metadata

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The two NOT NULL columns use the server_default → drop pattern
    # so existing rows on a populated DB get a sensible default.
    op.add_column(
        "custom_fields",
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default="manual",
        ),
    )
    op.alter_column("custom_fields", "source", server_default=None)
    op.add_column(
        "custom_fields",
        sa.Column("original_value", sa.String(length=1024), nullable=True),
    )

    op.add_column("parts", sa.Column("linked_provider", sa.String(length=40), nullable=True))
    op.add_column("parts", sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "parts",
        sa.Column(
            "description_locally_edited",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("parts", "description_locally_edited", server_default=None)


def downgrade() -> None:
    op.drop_column("parts", "description_locally_edited")
    op.drop_column("parts", "last_refresh_at")
    op.drop_column("parts", "linked_provider")
    op.drop_column("custom_fields", "original_value")
    op.drop_column("custom_fields", "source")
