"""Add per-BOM-line attrition (waste rate) to project_entries.

Revision ID: 0072
Revises: 0071
Create Date: 2026-09-05

Track B1 — mirror PartsBox's per-BOM-line "attrition": a waste percentage
that inflates the quantity a build requires and consumes so process scrap
does not create artificial shortages. This is additive to the existing
part-intrinsic attrition (`parts.attrition_percentage` /
`attrition_min_quantity`); the two multipliers compound in
`builds/service.py::_required`.

`attrition_pct` is `NOT NULL server_default '0'` so the column adds safely
to a populated table, and carries a CHECK `0 <= attrition_pct < 100`. The
upper bound stays strictly below 100 (a 100% waste line would demand
infinite stock to yield one placed part).

Numbering: this revision was authored as 0071 but renumbered to 0072 to
avoid colliding with a sibling PR (labels/print-jobs) that owns 0071 and
merges first. down_revision is therefore 0071, giving the linear chain
0070 -> 0071 (print jobs) -> 0072 (this).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None

_CK_ATTRITION = "ck_project_entries_attrition_pct_range"


def upgrade() -> None:
    op.add_column(
        "project_entries",
        sa.Column(
            "attrition_pct",
            sa.Numeric(6, 4),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        _CK_ATTRITION,
        "project_entries",
        "attrition_pct >= 0 AND attrition_pct < 100",
    )


def downgrade() -> None:
    op.drop_constraint(_CK_ATTRITION, "project_entries", type_="check")
    op.drop_column("project_entries", "attrition_pct")
