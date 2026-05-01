"""invitation tokens stored hashed at rest

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-02

The plaintext invitation token used to sit on every WorkspaceInvitation
row. A DB dump (legitimate backup, replica leak, ransomware, log line)
exposed every pending invitation as a replayable credential. This
migration switches to storing only `sha256(token)` — the plaintext is
returned to the caller exactly once at creation time and never lands
in any persisted artifact again.

Behaviour:
- Add `token_hash CHAR(64)` (SHA-256 hex digest) column.
- Backfill from existing `token` values via Python (no plaintext
  ever lands in the migration's stdout, so a logged ALTER trace
  doesn't leak them).
- Drop the unique constraint + plaintext `token` column.
- Add unique constraint on `token_hash`.

Downgrade is structurally reversible (re-add `token` column) but
cannot recover the plaintext — legacy rows would have NULL tokens
and nobody could accept those invitations. Documented in the
downgrade docstring; in practice if we need to roll back we'd
restore from the pre-deploy `pg_dump` instead.
"""
from __future__ import annotations

import hashlib

from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add the new column nullable so we can backfill before tightening.
    op.add_column(
        "workspace_invitations",
        sa.Column("token_hash", sa.String(length=64), nullable=True),
    )

    # 2. Backfill each row's hash. Done in Python to keep raw plaintext
    #    out of any SQL log / replay artifact.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, token FROM workspace_invitations")
    ).fetchall()
    for row_id, plaintext in rows:
        if plaintext is None:
            continue
        digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        bind.execute(
            sa.text(
                "UPDATE workspace_invitations SET token_hash = :h WHERE id = :i"
            ),
            {"h": digest, "i": row_id},
        )

    # 3. Tighten + swap constraints.
    op.alter_column("workspace_invitations", "token_hash", nullable=False)
    op.drop_constraint("uq_workspace_invitation_token", "workspace_invitations", type_="unique")
    op.drop_column("workspace_invitations", "token")
    op.create_unique_constraint(
        "uq_workspace_invitation_token_hash",
        "workspace_invitations",
        ["token_hash"],
    )


def downgrade() -> None:
    """Structurally reversible only. Hashes are one-way; the plaintext
    cannot be recovered from `token_hash`. After downgrade, legacy rows
    have `token = NULL` and cannot be accepted. If a real rollback is
    needed, restore from the pre-deploy `pg_dump` instead."""
    op.drop_constraint(
        "uq_workspace_invitation_token_hash", "workspace_invitations", type_="unique"
    )
    op.add_column(
        "workspace_invitations",
        sa.Column("token", sa.String(length=120), nullable=True),
    )
    op.create_unique_constraint(
        "uq_workspace_invitation_token", "workspace_invitations", ["token"]
    )
    op.drop_column("workspace_invitations", "token_hash")
