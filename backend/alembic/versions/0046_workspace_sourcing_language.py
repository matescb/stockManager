"""Add optional TrustedParts sourcing language code.

Revision ID: 0046
Revises: 0045
Create Date: 2026-05-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

_VALID_LANGUAGE_CODES = (
    "de",
    "en",
    "es",
    "fr",
    "it",
    "pt",
    "ja",
    "zh-hans",
    "zh-hant",
)


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("sourcing_language_code", sa.String(length=10), nullable=True),
    )
    op.create_check_constraint(
        "ck_workspaces_sourcing_language_code",
        "workspaces",
        "sourcing_language_code IS NULL OR sourcing_language_code IN "
        f"{_VALID_LANGUAGE_CODES!r}",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_workspaces_sourcing_language_code",
        "workspaces",
        type_="check",
    )
    op.drop_column("workspaces", "sourcing_language_code")
