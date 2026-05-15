"""Backfill stale invitation expiry windows (AUD-078).

Revision ID: 0059
Revises: 0058
Create Date: 2026-05-15

Migration 0053 added ``expires_at`` with a server default of
``now() + INTERVAL '14 days'``. PostgreSQL evaluated that default while
adding the column, which gave old pending invitations a fresh window.
This follow-up clamps already-stale pending rows back to their original
14-day expiry window and records an audit summary per affected workspace.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def expire_stale_invitations(conn: Connection) -> None:
    conn.execute(
        sa.text(
            """
            WITH expired AS (
                UPDATE workspace_invitations
                SET expires_at = LEAST(
                    expires_at,
                    created_at + INTERVAL '14 days'
                )
                WHERE status = 'pending'
                  AND created_at + INTERVAL '14 days' < now()
                  AND expires_at > created_at + INTERVAL '14 days'
                RETURNING id, workspace_id
            ),
            summary AS (
                SELECT
                    workspace_id,
                    array_agg(id ORDER BY id) AS target_ids,
                    count(*) AS expired_count
                FROM expired
                GROUP BY workspace_id
            )
            INSERT INTO audit_log (
                id,
                workspace_id,
                user_id,
                action,
                target_type,
                target_ids,
                comment,
                request_id,
                created_at
            )
            SELECT
                gen_random_uuid(),
                workspace_id,
                NULL,
                'invitation.stale_expiration_backfilled',
                'invitation',
                target_ids,
                format(
                    'AUD-078 expired %s stale pending workspace invitation(s) '
                    'at their original 14-day window.',
                    expired_count
                ),
                'migration-0059-aud-078',
                now()
            FROM summary
            """
        )
    )


def upgrade() -> None:
    expire_stale_invitations(op.get_bind())


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM audit_log
            WHERE action = 'invitation.stale_expiration_backfilled'
              AND request_id = 'migration-0059-aud-078'
            """
        )
    )
