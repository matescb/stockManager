"""Add expiry timestamp to workspace invitations.

Revision ID: 0053
Revises: 0052
Create Date: 2026-05-14
"""

import sqlalchemy as sa

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_invitations",
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + INTERVAL '14 days'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("workspace_invitations", "expires_at")
