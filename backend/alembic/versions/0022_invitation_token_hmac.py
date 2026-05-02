"""Add token_hmac column to workspace_invitations (SEC2-013).

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-02

SEC2-013 fix: the invitation accept flow previously queried
`WHERE token_hash = $digest` (SQL equality on a string), which is a
timing oracle — an attacker who can observe response latency can
distinguish "no row found" from "row found, wrong digest" because
Postgres string equality is not constant-time.

The fix:
1. Store an HMAC-SHA-256 digest (keyed on SESSION_SECRET) alongside
   the existing SHA-256 hash.  The HMAC key means the server-side
   secret must be known to forge a valid lookup even if the DB is
   leaked.
2. The accept flow looks up by `id` (PK — no timing oracle) then
   calls `hmac.compare_digest(hmac_of_supplied, row.token_hmac)` so
   the comparison is constant-time.

Backfill note:
  Plaintexts are never stored, so existing `token_hash` values cannot
  be converted to `token_hmac` values without knowing the plaintext.
  Any pending invitation created before this migration is deployed
  will have `token_hmac = NULL` and will be rejected at accept time.
  Operators should revoke and re-issue outstanding invitations after
  the deploy.  This is documented in CHANGELOG.md under SEC2-013.

Downgrade:
  Drops the `token_hmac` column; the old `WHERE token_hash = $digest`
  path is restored by reverting the application code (the `token_hash`
  column and its unique index are not touched by this migration).
"""
from alembic import op
import sqlalchemy as sa


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_invitations",
        sa.Column("token_hmac", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspace_invitations", "token_hmac")
