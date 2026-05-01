"""no-op stub left after the encrypt-workspace-secrets revert

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-02

The original 0015 (`feat: encrypt workspace secrets at rest`) was
reverted on prod after a 502 emergency — the migration's guardrail
failed at deploy time because `WORKSPACE_SECRETS_KEY` wasn't wired
into the prod env and there were existing provider credentials.

If prod's `alembic_version` table happened to land at 0015 before the
container crashed (i.e., the schema-change part of the migration ran
but the backfill failed), deleting the file outright would leave the
chain broken — `alembic upgrade head` would error with "Can't locate
revision identified by '0015'". This stub keeps the revision ID in
the chain so the upgrade is a no-op regardless of which side of the
failure prod ended up on.

When the proper encrypt-at-rest work re-lands, it goes in as 0016 (or
later) and supersedes this stub. Don't delete this file even after
that re-land — the prod alembic_version chain needs it.
"""
from __future__ import annotations


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
