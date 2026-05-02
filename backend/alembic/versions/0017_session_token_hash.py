"""session tokens stored hashed at rest + sliding expiry

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-02

Mirror of the invitation token hashing landed in 0014, applied to
`user_sessions` (SEC2-003). The plaintext session token used to be
the primary key of every row, so a DB dump leaked every active
session as a replayable cookie. We now store only `sha256(token)` in
`token_hash`. The plaintext lives only on the client cookie and is
never persisted.

Plus a new `last_used_at` column for sliding expiry (SEC2-015): the
deps layer bumps it on every successful auth lookup, and rejects
sessions idle for longer than the configured window (24h at the
time of writing) even when the absolute `expires_at` is still in
the future.

Behaviour:
- Drop the `user_sessions.token` column entirely. We don't try to
  backfill `token_hash` from existing rows because the schema swap
  is destructive (the old PK goes away) and the user pool is small
  enough that forcing every active session to re-login is acceptable.
  All existing sessions are invalidated by this migration — that's
  the documented trade-off.
- Add `token_hash CHAR(64) PRIMARY KEY`.
- Add `last_used_at TIMESTAMPTZ NOT NULL DEFAULT now()`.

Downgrade is structurally reversible (re-add `token` column + drop
`token_hash` / `last_used_at`) but the original plaintext tokens are
unrecoverable. Rolling back further than 0016 forces every session
to re-login a second time — fine, since 0017 already invalidated
them all.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Wipe every existing row so the swap from `token`-keyed to
    # `token_hash`-keyed is unambiguous. There is no migration path
    # that keeps current sessions valid without storing plaintext on
    # the server, and that's the whole point of this change. All
    # users will be forced to re-login post-deploy — pre-merge note
    # in the PR body warns operators.
    op.execute("DELETE FROM user_sessions")

    # Drop the old plaintext PK column. PK constraint name is
    # `user_sessions_pkey` by Postgres default, dropped by `op.drop_column`
    # automatically.
    op.drop_column("user_sessions", "token")

    # Add the new hashed PK. CHAR(64) is the exact length of a hex
    # SHA-256 digest; using VARCHAR(64) keeps schema-comparison tools
    # quieter without changing storage in Postgres.
    op.add_column(
        "user_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
    )
    op.create_primary_key(
        "user_sessions_pkey",
        "user_sessions",
        ["token_hash"],
    )

    # Sliding-expiry tracking. `server_default=text('now()')` so the
    # column is populated on every existing row at upgrade time —
    # though there are no rows after the DELETE above, this keeps the
    # migration idempotent for any test/dev DB that re-applies it.
    op.add_column(
        "user_sessions",
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    """Structurally reversible. The original plaintext tokens are gone
    forever; downgraded sessions cannot be authenticated until the
    user logs in again. Wipe the table on the way down so the
    re-introduced `token NOT NULL` PK is clean."""
    op.execute("DELETE FROM user_sessions")
    op.drop_column("user_sessions", "last_used_at")
    op.drop_constraint("user_sessions_pkey", "user_sessions", type_="primary")
    op.drop_column("user_sessions", "token_hash")
    op.add_column(
        "user_sessions",
        sa.Column("token", sa.String(length=120), nullable=False),
    )
    op.create_primary_key(
        "user_sessions_pkey",
        "user_sessions",
        ["token"],
    )
