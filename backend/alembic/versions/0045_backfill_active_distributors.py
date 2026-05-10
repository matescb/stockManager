"""Backfill active sourcing lists from saved workspace defaults.

Revision ID: 0045
Revises: 0044
Create Date: 2026-05-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def backfill_active_lists(conn: Connection) -> None:
    conn.execute(
        sa.text(
            """
            UPDATE workspaces AS ws
            SET active_distributors = (
                SELECT COALESCE(
                    jsonb_agg(to_jsonb(value) ORDER BY first_seen),
                    '[]'::jsonb
                ) AS values
                FROM (
                    SELECT value, min(ord) AS first_seen
                    FROM (
                        SELECT value, ord
                        FROM jsonb_array_elements_text(
                            COALESCE(ws.active_distributors, '[]'::jsonb)
                        ) WITH ORDINALITY AS active(value, ord)
                        UNION ALL
                        SELECT value, ord + 1000000
                        FROM jsonb_array_elements_text(
                            COALESCE(ws.sourcing_preferred_distributors, '[]'::jsonb)
                        ) WITH ORDINALITY AS preferred(value, ord)
                    ) combined
                    GROUP BY value
                ) deduped
            )
            WHERE ws.sourcing_preferred_distributors IS NOT NULL
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE workspaces
            SET active_countries =
                CASE
                    WHEN COALESCE(active_countries, '[]'::jsonb) ? sourcing_country_code
                    THEN active_countries
                    ELSE COALESCE(active_countries, '[]'::jsonb) || to_jsonb(sourcing_country_code)
                END
            WHERE sourcing_country_code IS NOT NULL
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE workspaces
            SET active_currencies =
                CASE
                    WHEN COALESCE(active_currencies, '[]'::jsonb) ? sourcing_currency_code
                    THEN active_currencies
                    ELSE COALESCE(active_currencies, '[]'::jsonb)
                        || to_jsonb(sourcing_currency_code)
                END
            WHERE sourcing_currency_code IS NOT NULL
            """
        )
    )


def upgrade() -> None:
    backfill_active_lists(op.get_bind())


def downgrade() -> None:
    # No-op: unmerging these active-list values would remove user data.
    pass
