"""Partial unique index on workspace_invitations(workspace_id, email)
WHERE status = 'pending' to prevent duplicate pending invitations for
the same email in the same workspace.

Revision ID: 0023
Revises: 0021
Create Date: 2026-05-02

BE2-020 / issue #65. Two concurrent admin POSTs can mint two pending
invites for the same (workspace_id, email) pair if both pass the
existence check before either commits. This partial unique index turns
the second insert into an IntegrityError that the application layer can
catch and handle gracefully (return the existing pending row).

The index is partial (WHERE status = 'pending') so that:
- Accepting an invitation (status -> 'accepted') allows a new pending
  invite for the same email later (e.g. re-inviting after role change).
- Revoking (status -> 'revoked') similarly frees the slot.

Chain note: migration 0022 is claimed by issue #63 (search unbounded
query length) which is in-progress; this PR takes 0023.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0023"
down_revision = "0021"
branch_labels = None
depends_on = None


_INDEX_NAME = "uq_workspace_invitation_pending"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "workspace_invitations",
        ["workspace_id", "email"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="workspace_invitations")
