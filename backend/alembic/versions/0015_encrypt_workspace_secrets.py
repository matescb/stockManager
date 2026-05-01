"""encrypt workspace-level secrets at rest

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-02

The 2026-04-30 review's Sec HIGH-9: workspace.parts_provider_api_key /
parts_provider_api_secret / scanner_license_key were stored as
plaintext columns. A DB dump leaked every workspace's third-party
credentials.

Behaviour:
  * Bump column lengths so a Fernet ciphertext (~30% larger than
    plaintext after base64) fits with headroom.
      - parts_provider_api_key   String(255)  -> String(1024)
      - parts_provider_api_secret String(255)  -> String(1024)
      - scanner_license_key      String(2048) -> String(4096)
  * Encrypt every existing non-NULL row in place via Python (using
    `app.core.secrets.encrypt`). Reading the plaintext directly into
    Python keeps the values out of any SQL trace / log.
  * The columns stay nullable; route layer encrypts on PATCH and
    decrypts on read.

Operational hazard:
  Lose `WORKSPACE_SECRETS_KEY` and every credential becomes
  unrecoverable. The dev fallback key in `app/core/secrets.py` keeps
  local-first runs zero-config; prod must set the env var. Document
  the key in escrow alongside SESSION_SECRET.

Downgrade:
  Schema reversal works (column lengths shrink back), but cannot
  recover plaintext from a key that was lost. If a real rollback is
  needed, restore from the pre-deploy `pg_dump` instead.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Lengthen columns first so post-encryption ciphertexts fit.
    op.alter_column(
        "workspaces", "parts_provider_api_key",
        existing_type=sa.String(255),
        type_=sa.String(1024),
        existing_nullable=True,
    )
    op.alter_column(
        "workspaces", "parts_provider_api_secret",
        existing_type=sa.String(255),
        type_=sa.String(1024),
        existing_nullable=True,
    )
    op.alter_column(
        "workspaces", "scanner_license_key",
        existing_type=sa.String(2048),
        type_=sa.String(4096),
        existing_nullable=True,
    )

    # Backfill: encrypt every existing non-empty value. Done in Python
    # so the plaintext never appears in a SQL log line.
    from app.core.config import settings
    from app.core.secrets import encrypt, safe_decrypt

    bind = op.get_bind()

    # Guardrail: if there are non-NULL credentials AND the operator
    # forgot to set WORKSPACE_SECRETS_KEY in env, refuse to run rather
    # than silently encrypt under the dev fallback key (which would
    # then 500 every credential read at runtime when prod expects a
    # real key). Empty DB / no credentials → nothing to encrypt → safe.
    has_creds = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM workspaces WHERE "
            " parts_provider_api_key IS NOT NULL OR"
            " parts_provider_api_secret IS NOT NULL OR"
            " scanner_license_key IS NOT NULL"
        )
    ).scalar()
    if has_creds and not settings().WORKSPACE_SECRETS_KEY:
        raise RuntimeError(
            "0015: refusing to encrypt under the dev fallback key. "
            "Set WORKSPACE_SECRETS_KEY in env (Fernet-generated) before "
            "running this migration against non-empty data."
        )

    rows = bind.execute(
        sa.text(
            "SELECT id, parts_provider_api_key, parts_provider_api_secret, scanner_license_key "
            "FROM workspaces"
        )
    ).fetchall()
    # Idempotency: `safe_decrypt` round-trips an already-encrypted
    # token through decrypt -> plaintext, then re-encrypts. On a fresh
    # plaintext row, safe_decrypt returns the input unchanged so
    # encrypt() produces the first ciphertext. On an already-encrypted
    # row (from a prior partial-failure retry), safe_decrypt returns
    # the plaintext and encrypt() produces a fresh ciphertext bound to
    # the same plaintext. Either way the post-migration plaintext
    # matches the pre-migration plaintext.
    for row_id, key, secret, license_key in rows:
        bind.execute(
            sa.text(
                "UPDATE workspaces SET "
                " parts_provider_api_key = :k,"
                " parts_provider_api_secret = :s,"
                " scanner_license_key = :l "
                "WHERE id = :i"
            ),
            {
                "k": encrypt(safe_decrypt(key)),
                "s": encrypt(safe_decrypt(secret)),
                "l": encrypt(safe_decrypt(license_key)),
                "i": row_id,
            },
        )


def downgrade() -> None:
    """Schema reversal only; plaintext cannot be recovered from a key
    rotation. Restore from `pg_dump` for a real rollback."""
    op.alter_column(
        "workspaces", "scanner_license_key",
        existing_type=sa.String(4096),
        type_=sa.String(2048),
        existing_nullable=True,
    )
    op.alter_column(
        "workspaces", "parts_provider_api_secret",
        existing_type=sa.String(1024),
        type_=sa.String(255),
        existing_nullable=True,
    )
    op.alter_column(
        "workspaces", "parts_provider_api_key",
        existing_type=sa.String(1024),
        type_=sa.String(255),
        existing_nullable=True,
    )
