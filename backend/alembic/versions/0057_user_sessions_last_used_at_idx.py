"""Index user_sessions.last_used_at for idle-session purge.

Revision ID: 0057
Revises: 0056
Create Date: 2026-05-15

AUD-075 / issue #713. The periodic session purge now removes rows that
would already be rejected by the auth-time idle timeout. Indexing
last_used_at keeps that cutoff delete seekable as user_sessions grows.
"""

from __future__ import annotations

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_user_sessions_last_used_at",
        "user_sessions",
        ["last_used_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_sessions_last_used_at",
        table_name="user_sessions",
    )
