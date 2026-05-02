"""Add catalog_token_hash column for constant-time token lookup.

Revision ID: 0025
Revises: 0023
Create Date: 2026-05-02

SEC2-008 / issue #71.

The existing catalog_token column stores the token in plaintext, enabling
a timing-observable SQL equality scan (`WHERE catalog_token = ?`). This
migration:

1. Adds `catalog_token_hash` (String(64), unique-indexed, nullable).
2. Backfills existing rows using HMAC-SHA256 keyed by SESSION_SECRET.
   If SESSION_SECRET is unavailable at migration time the backfill falls
   back to plain SHA-256(catalog_token) and logs a warning — the
   application code always writes the HMAC form for new/rotated tokens
   so the legacy rows will be superseded on first regeneration.
3. Keeps `catalog_token` nullable for rollback-safety (the old column is
   never read by the application after this migration).

The unique index is created WITHOUT NOT NULL so it gracefully handles
the (legitimate) case where catalog_token is NULL.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from alembic import op
import sqlalchemy as sa


revision = "0025"
down_revision = "0023"
branch_labels = None
depends_on = None


_COLUMN = "catalog_token_hash"
_INDEX = "ix_workspaces_catalog_token_hash"


def _hmac_hex(secret: str, token: str) -> str:
    return hmac.new(
        secret.encode(),
        token.encode(),
        hashlib.sha256,
    ).hexdigest()


def _sha256_hex(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def upgrade() -> None:
    # 1. Add the hash column (nullable so the statement is instant on
    #    a live table — no NOT NULL + DEFAULT scan needed).
    op.add_column(
        "workspaces",
        sa.Column(_COLUMN, sa.String(64), nullable=True),
    )

    # 2. Backfill existing rows that have a catalog_token set.
    #    We do the computation in Python so we can use the same HMAC
    #    logic as the application rather than a Postgres extension.
    bind = op.get_bind()
    secret = os.environ.get("SESSION_SECRET", "")
    use_hmac = bool(secret)

    rows = bind.execute(
        sa.text("SELECT id, catalog_token FROM workspaces WHERE catalog_token IS NOT NULL")
    ).fetchall()

    for row in rows:
        ws_id, token = row[0], row[1]
        if not token:
            continue
        digest = _hmac_hex(secret, token) if use_hmac else _sha256_hex(token)
        bind.execute(
            sa.text(
                "UPDATE workspaces SET catalog_token_hash = :digest WHERE id = :id"
            ),
            {"digest": digest, "id": ws_id},
        )

    if rows and not use_hmac:
        import warnings
        warnings.warn(
            "SESSION_SECRET not set at migration time; catalog_token_hash "
            "was backfilled with plain SHA-256 instead of HMAC. "
            "Rotate catalog tokens after setting SESSION_SECRET to upgrade "
            "those rows to the HMAC form.",
            stacklevel=1,
        )

    # 3. Unique index (partial: only non-NULL hashes need to be unique).
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} "
        f"ON workspaces ({_COLUMN}) "
        f"WHERE {_COLUMN} IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    op.drop_column("workspaces", _COLUMN)
