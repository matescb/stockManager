"""Add TrustedParts gap-field sourcing alerts.

Revision ID: 0047
Revises: 0046
Create Date: 2026-05-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

_ORIGINAL_ALERT_TYPES = (
    "stock_below",
    "stock_above",
    "back_in_stock",
    "out_of_authorized_stock",
    "price_changed",
    "bom_buyable",
)
_NEW_ALERT_TYPES = (
    "lifecycle_risk_changed",
    "supply_chain_risk_changed",
    "tariff_status_changed",
)


def _alert_type_check(values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"alert_type IN ({quoted})"


def upgrade() -> None:
    op.drop_constraint(
        "sourcing_alerts_alert_type_check",
        "sourcing_alerts",
        type_="check",
    )
    op.create_check_constraint(
        "sourcing_alerts_alert_type_check",
        "sourcing_alerts",
        _alert_type_check(_ORIGINAL_ALERT_TYPES + _NEW_ALERT_TYPES),
    )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT count(*) FROM sourcing_alerts "
            "WHERE alert_type IN :alert_types"
        ).bindparams(sa.bindparam("alert_types", expanding=True)),
        {"alert_types": _NEW_ALERT_TYPES},
    ).scalar_one()
    if rows:
        raise RuntimeError(
            "Cannot downgrade while sourcing_alerts rows use "
            "lifecycle_risk_changed, supply_chain_risk_changed, or "
            "tariff_status_changed. Delete those alerts first."
        )

    op.drop_constraint(
        "sourcing_alerts_alert_type_check",
        "sourcing_alerts",
        type_="check",
    )
    op.create_check_constraint(
        "sourcing_alerts_alert_type_check",
        "sourcing_alerts",
        _alert_type_check(_ORIGINAL_ALERT_TYPES),
    )
