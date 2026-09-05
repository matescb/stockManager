"""Read-only inventory reports."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.api._helpers import assert_in_workspace
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession, require_role
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import ok
from app.domain._quantity import QUANTITY_ZERO, quantity_out
from app.domain.builds.service import shortage_analysis, shortage_rows_out
from app.domain.lots.models import Lot
from app.domain.parts.models import Part
from app.domain.projects.models import Project
from app.domain.reports.schemas import ReplenishmentCostSort
from app.domain.reports.service import (
    bom_buyability_report,
    low_stock_report,
    replenishment_cost_report,
    sourcing_risk_report,
)
from app.domain.stock.service import (
    bulk_current_quantities_by_lot,
)

router = APIRouter()

_MONEY_ZERO = Decimal("0")


def _money_out(value: Decimal) -> float:
    """Money for an untyped response dict.

    JSON has no decimal type, so a monetary total has to become a JSON
    number here. The point of the exact accumulation upstream is that this
    conversion happens **once**, on a value that is already correct, rather
    than on every intermediate product.
    """
    return float(round(value, 6))


@router.get("/low-stock")
def low_stock(
    db: DbSession,
    ws: CurrentWorkspace,
    include_sourcing: bool = Query(default=False),
):
    """Parts whose *available* (on-hand minus reserved) is below their
    `low_stock_report_quantity`. Parts without a threshold are skipped."""
    return ok(low_stock_report(db, workspace=ws, include_sourcing=include_sourcing))


@router.get("/sourcing-risk", dependencies=[Depends(require_role("member"))])
@limiter.limit("30/minute", key_func=workspace_key)
def sourcing_risk(
    request: Request,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
    only_with_flags: bool = Query(default=True),
    use_cached_data: bool | None = Query(default=None),
):
    out = sourcing_risk_report(
        db,
        workspace=ws,
        only_with_flags=only_with_flags,
        use_cached_data=use_cached_data,
        requested_by=user.id,
    )
    return ok(out.model_dump(mode="json"))


@router.get("/bom-shortage")
def bom_shortage(
    db: DbSession,
    ws: CurrentWorkspace,
    project_id: UUID,
    quantity: int = Query(default=1, gt=0),
):
    """Project-wide shortage analysis at a given build quantity.
    Same engine as Build detail — no build is created."""
    try:
        project = assert_in_workspace(db, Project, project_id, ws.id, label="project")
    except HTTPException:
        raise_http(404, code=ErrorCodes.REPORT_PROJECT_NOT_FOUND, message="project not found")
    rows = shortage_analysis(db, workspace_id=ws.id, project=project, build_quantity=quantity)
    total_short = sum((r["short_by"] for r in rows), QUANTITY_ZERO)
    return ok(
        {
            "project_id": str(project_id),
            "quantity": quantity,
            "rows": shortage_rows_out(rows),
            "total_short": quantity_out(total_short),
        }
    )


@router.get(
    "/bom-buyability",
    dependencies=[Depends(require_role("member"))],
)
@limiter.limit("30/minute", key_func=workspace_key)
def bom_buyability(
    request: Request,
    db: DbSession,
    ws: CurrentWorkspace,
    build_quantity: int = Query(default=1, ge=1),
):
    """Workspace-wide per-project BOM buyability report."""
    out = bom_buyability_report(
        db,
        workspace=ws,
        build_quantity=build_quantity,
        use_cached_data=True,
    )
    return ok(out.model_dump(mode="json"))


@router.get("/stock-value")
def stock_value(db: DbSession, ws: CurrentWorkspace):
    """Sum of (lot.purchase_unit_cost × current_qty_in_lot) across all
    on-hand stock, broken down by currency. Untaged lots (no purchase
    cost) are listed under value 0."""
    # Per-lot current qty — single SQL via bulk_current_quantities_by_lot
    # so the SUM-of-delta invariant lives in one place (BE2-005).
    lot_qty = bulk_current_quantities_by_lot(db, workspace_id=ws.id)
    lots = {l.id: l for l in db.query(Lot).filter(Lot.workspace_id == ws.id).all()}
    parts = {p.id: p for p in db.query(Part).filter(Part.workspace_id == ws.id).all()}

    # Quantity x price is accumulated in exact Decimal. This used to
    # truncate the lot quantity with `int(qty)` and downcast the unit cost
    # to a binary `float` before multiplying, so every extended value —
    # and the per-currency total accumulated from them — carried float
    # drift. `Numeric(18,6) x Numeric(18,6)` is exact; the single
    # `float()` below is the JSON boundary, where money has to become a
    # JSON number anyway.
    by_currency: dict[str | None, Decimal] = defaultdict(lambda: _MONEY_ZERO)
    by_part: dict[UUID, dict] = {}
    for lot_id, qty in lot_qty.items():
        if qty <= 0:
            continue
        lot = lots.get(lot_id)
        if lot is None:
            continue
        unit_cost = lot.purchase_unit_cost if lot.purchase_unit_cost is not None else _MONEY_ZERO
        currency = lot.purchase_currency
        value = unit_cost * qty
        by_currency[currency] += value
        bp = by_part.setdefault(
            lot.part_id,
            {
                "part_id": str(lot.part_id),
                "name": parts[lot.part_id].name if lot.part_id in parts else str(lot.part_id),
                "on_hand": QUANTITY_ZERO,
                "value": _MONEY_ZERO,
                "currency": currency,
            },
        )
        bp["on_hand"] += qty
        bp["value"] += value
        # If multiple currencies on one part, leave as the first seen + flag
        if bp["currency"] and currency and bp["currency"] != currency:
            bp["currency"] = "MIXED"

    by_part_rows = sorted(by_part.values(), key=lambda r: r["value"], reverse=True)
    return ok(
        {
            "by_currency": [
                {"currency": cur, "value": _money_out(v)}
                for cur, v in sorted(by_currency.items(), key=lambda kv: (kv[0] or ""))
            ],
            "by_part": [
                {**row, "on_hand": quantity_out(row["on_hand"]), "value": _money_out(row["value"])}
                for row in by_part_rows
            ],
        }
    )


@router.get("/replenishment-cost")
@limiter.limit("30/minute", key_func=workspace_key)
def replenishment_cost(
    request: Request,
    db: DbSession,
    ws: CurrentWorkspace,
    sort: ReplenishmentCostSort = Query(default="delta_pct"),
    use_cached_data: bool | None = None,
):
    out = replenishment_cost_report(
        db,
        workspace=ws,
        use_cached_data=use_cached_data,
        sort=sort,
    )
    return ok(out.model_dump(mode="json"))


@router.get("/expiring-lots")
def expiring_lots(
    db: DbSession,
    ws: CurrentWorkspace,
    days: int = Query(default=90, ge=0, le=3650),
):
    """Lots expiring within the next `days` days (or already expired) that
    still have on-hand stock."""
    cutoff = date.today() + timedelta(days=days)
    lot_qty = bulk_current_quantities_by_lot(db, workspace_id=ws.id)
    rows = list(
        db.execute(
            select(Lot)
            .where(Lot.workspace_id == ws.id)
            .where(Lot.expiration_date.is_not(None))
            .where(Lot.expiration_date <= cutoff)
            .order_by(Lot.expiration_date)
        ).scalars()
    )
    parts = {p.id: p for p in db.query(Part).filter(Part.workspace_id == ws.id).all()}
    out = []
    today = date.today()
    for l in rows:
        qty = lot_qty.get(l.id, QUANTITY_ZERO)
        if qty <= 0:
            continue
        days_left = (l.expiration_date - today).days
        out.append(
            {
                "lot_id": str(l.id),
                "name": l.name,
                "part_id": str(l.part_id),
                "part_name": parts[l.part_id].name if l.part_id in parts else None,
                "on_hand": quantity_out(qty),
                "expiration_date": l.expiration_date.isoformat(),
                "days_until_expiry": days_left,
                "expired": days_left < 0,
            }
        )
    return ok(out)
