"""encrypt workspace-level secrets at rest

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-02

The 2026-04-30 review's Sec HIGH-9: workspace.parts_provider_api_key /
parts_provider_api_secret / scanner_license_key were stored as
plaintext columns. A DB dump leaked every workspace's third-party
credentials.

This is the second attempt to land the encrypt-at-rest work; the
first (0015) was reverted on prod after a 502 emergency — the
guardrail there raised RuntimeError when WORKSPACE_SECRETS_KEY was
unset, killing the deploy. Lesson: a soft warning is the right
posture for an env var that has a working dev fallback.

Behaviour:
  * Bump column lengths so a Fernet ciphertext (~30% larger than
    plaintext after base64) fits with headroom.
      - parts_provider_api_key   String(255)  -> String(1024)
      - parts_provider_api_secret String(255)  -> String(1024)
      - scanner_license_key      String(2048) -> String(4096)
  * Encrypt every existing non-NULL row in place via Python (using
    `app.core.secrets.encrypt`). Reading + writing the plaintext
    in Python keeps it out of any SQL trace / log.
  * `safe_decrypt(value)` before re-encrypt makes the migration
    idempotent: re-running after a partial-failure decrypts already-
    encrypted rows back to plaintext, then re-encrypts. Never
    double-encrypts.
  * If WORKSPACE_SECRETS_KEY is unset, the underlying Fernet falls
    back to the dev default key. `app.core.secrets._fernet()` emits
    a warning to the structured-logging foundation so the operator
    sees it on container start. No more RuntimeError that took prod
    down.

Operational hazard:
  Lose `WORKSPACE_SECRETS_KEY` and every credential becomes
  unrecoverable. Escrow alongside SESSION_SECRET. The dev fallback
  works for low-stakes envs; real prod should override.

Downgrade:
  Schema reversal (column lengths shrink). Cannot recover plaintext
  from a key that was lost. Restore from pg_dump for a real rollback.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Lengthen columns first so post-encryption ciphertexts fit. Idempotent
    # — if the previously-reverted 0015 had already widened these columns
    # (in the failure window before alembic_version was committed),
    # ALTER COLUMN with the same target type is a no-op rather than an
    # error.
    #
    # DB-011 / issue #102: widening varchar(N) -> varchar(M) where M > N is
    # a Postgres catalog-only change — no table rewrite, no USING clause,
    # no truncation risk. A future *shrink* (M < N) MUST add
    # `postgresql_using="left(col, M)"` and a regression test. See
    # docs/development.md -> "Migration patterns" for the asymmetry.
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
    from app.core.secrets import encrypt, safe_decrypt

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, parts_provider_api_key, parts_provider_api_secret, scanner_license_key "
            "FROM workspaces"
        )
    ).fetchall()
    for row_id, key, secret, license_key in rows:
        # Idempotency: safe_decrypt round-trips an already-encrypted
        # token back to plaintext. encrypt() produces fresh ciphertext
        # bound to the same plaintext, so re-running after a partial
        # failure never double-encrypts.
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
    rotation. Restore from pg_dump for a real rollback."""
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
