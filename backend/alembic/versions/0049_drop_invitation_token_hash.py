"""Drop legacy workspace invitation token_hash.

Revision ID: 0049
Revises: 0048
Create Date: 2026-05-14

Invitation acceptance has used token_hmac since 0022, so the older
plaintext SHA-256 token_hash no longer participates in lookups or
validation. Keeping it only widens the data exposed by a DB dump.
"""

import sqlalchemy as sa

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


_TABLE = "workspace_invitations"
_COLUMN = "token_hash"
_CONSTRAINT = "uq_workspace_invitation_token_hash"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="unique")
    op.drop_column(_TABLE, _COLUMN)


def downgrade() -> None:
    """Structurally re-add the legacy column for rollback.

    The SHA-256 digest cannot be recovered from token_hmac or from the
    one-time plaintext invitation token, so restored rows get NULL.
    PostgreSQL unique constraints allow multiple NULLs; newly created
    rows from rolled-back application code can still write real digests.
    """

    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(_CONSTRAINT, _TABLE, [_COLUMN])
