"""Multi-token catalog access: workspace_catalog_tokens child table.

Revision ID: 0035
Revises: 0034
Create Date: 2026-05-02

SEC2-019 / issue #77.

A single catalog token per workspace means a leaked token requires full
rotation, breaking all consumers. This migration:

1. Creates `workspace_catalog_tokens` with HMAC, label, revoked_at,
   last_used_at, last_used_ip, and a partial unique index on
   (workspace_id, token_hmac) WHERE revoked_at IS NULL.

2. Backfills one "default (legacy)" row per workspace that already has
   catalog_token_hash set, so existing tokens continue to work via the
   new table (catalog.py checks child table first, falls back to the
   legacy Workspace.catalog_token_hash column for any workspace that was
   not yet backfilled).

Downgrade: drop the table (legacy tokens survive in Workspace).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import uuid

import sqlalchemy as sa

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


_TABLE = "workspace_catalog_tokens"
_INDEX = "uq_catalog_tokens_ws_hmac_active"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("token_hmac", sa.String(64), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "created_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_ip", sa.String(45), nullable=True),
    )

    # Partial unique index: only unrevoked tokens need distinct HMACs per workspace.
    op.execute(
        f"CREATE UNIQUE INDEX {_INDEX} "
        f"ON {_TABLE} (workspace_id, token_hmac) "
        f"WHERE revoked_at IS NULL"
    )

    # Backfill: one "default (legacy)" row per workspace that has catalog_token_hash.
    bind = op.get_bind()
    secret = os.environ.get("SESSION_SECRET", "")

    rows = bind.execute(
        sa.text(
            "SELECT id, catalog_token, catalog_token_hash "
            "FROM workspaces "
            "WHERE catalog_token_hash IS NOT NULL"
        )
    ).fetchall()

    for row in rows:
        ws_id, token_plaintext, token_hash = row[0], row[1], row[2]

        # Determine the HMAC to store: ideally re-derive from plaintext so
        # both lookup paths use the same key.  If the plaintext is gone (NULL)
        # we fall back to the already-stored hash — the app's catalog.py checks
        # the child table first by re-hashing the candidate token, so the HMAC
        # must be consistent.
        if token_plaintext and secret:
            hmac_val = hmac.new(
                secret.encode(),
                token_plaintext.encode(),
                hashlib.sha256,
            ).hexdigest()
        else:
            # Plain hash already in catalog_token_hash; reuse it.
            hmac_val = token_hash

        row_id = str(uuid.uuid4())
        bind.execute(
            sa.text(
                f"INSERT INTO {_TABLE} "
                "(id, workspace_id, token_hmac, label, created_at) "
                "VALUES (:id, :ws_id, :hmac, :label, NOW())"
            ),
            {
                "id": row_id,
                "ws_id": str(ws_id),
                "hmac": hmac_val,
                "label": "default (legacy)",
            },
        )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    op.drop_table(_TABLE)
