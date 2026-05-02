"""Constrain project_entries.quantity to Integer and add non-negative
CHECK constraints on project_entries.quantity and order_entries quantities.

Revision ID: 0031
Revises: 0030
Create Date: 2026-05-02

DB-005 / issue #96. project_entries.quantity was Numeric(18,6) while
stock_entries.quantity_delta is Integer, causing silent precision loss
when a build consumed a BOM line with a fractional quantity.

Option (b) chosen: constrain BOM/order quantities to integer throughout
(electronics domain — no fractional quantities are needed).

Upgrade:
  1. Abort if any fractional rows exist in project_entries.
  2. ALTER project_entries.quantity from Numeric(18,6) to Integer.
  3. Add CHECK (quantity >= 0) on project_entries.
  4. Add CHECK (quantity_ordered >= 0) and CHECK (quantity_received >= 0)
     on order_entries (those columns were already Integer; the constraints
     tighten the invariant at the DB level).

Chain: 0029 (pending_users) -> 0030 (audit_log) -> 0031 (this).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_CK_PROJECT_QTY = "ck_project_entries_quantity_nonneg"
_CK_ORDER_QTY_ORDERED = "ck_order_entries_qty_ordered_nonneg"
_CK_ORDER_QTY_RECEIVED = "ck_order_entries_qty_received_nonneg"


def upgrade() -> None:
    conn = op.get_bind()

    # Guard: refuse to run if any project_entries row has a fractional quantity.
    row = conn.execute(
        sa.text("SELECT COUNT(*) FROM project_entries WHERE quantity != trunc(quantity)")
    ).scalar()
    if row:
        raise RuntimeError(
            f"Cannot migrate: {row} project_entries row(s) have fractional quantity values. "
            "Resolve them manually before running this migration."
        )

    # ALTER project_entries.quantity Numeric(18,6) → Integer
    op.alter_column(
        "project_entries",
        "quantity",
        type_=sa.Integer(),
        postgresql_using="quantity::integer",
        nullable=False,
    )

    # CHECK constraints
    op.create_check_constraint(
        _CK_PROJECT_QTY,
        "project_entries",
        "quantity >= 0",
    )
    op.create_check_constraint(
        _CK_ORDER_QTY_ORDERED,
        "order_entries",
        "quantity_ordered >= 0",
    )
    op.create_check_constraint(
        _CK_ORDER_QTY_RECEIVED,
        "order_entries",
        "quantity_received >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(_CK_ORDER_QTY_RECEIVED, "order_entries", type_="check")
    op.drop_constraint(_CK_ORDER_QTY_ORDERED, "order_entries", type_="check")
    op.drop_constraint(_CK_PROJECT_QTY, "project_entries", type_="check")

    op.alter_column(
        "project_entries",
        "quantity",
        type_=sa.Numeric(18, 6),
        postgresql_using="quantity::numeric(18,6)",
        nullable=False,
    )
