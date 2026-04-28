"""Read-only inventory reports."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.core.deps import CurrentWorkspace, DbSession
from app.core.responses import ok
from app.domain.builds.service import shortage_analysis
from app.domain.lots.models import Lot
from app.domain.parts.models import Part
from app.domain.projects.models import Project
from app.domain.stock.models import StockEntry

router = APIRouter()


@router.get("/low-stock")
def low_stock(db: DbSession, ws: CurrentWorkspace):
    """Parts whose on-hand is below their `low_stock_report_quantity`.
    Parts without a threshold are skipped."""
    parts = list(
        db.execute(
            select(Part)
            .where(Part.workspace_id == ws.id)
            .where(Part.archived_at.is_(None))
            .where(Part.low_stock_report_quantity.is_not(None))
        ).scalars()
    )
    on_hand_rows = db.execute(
        select(StockEntry.part_id, func.coalesce(func.sum(StockEntry.quantity_delta), 0))
        .where(StockEntry.workspace_id == ws.id)
        .where(StockEntry.status == "on_hand")
        .group_by(StockEntry.part_id)
    ).all()
    on_hand = {row[0]: int(row[1]) for row in on_hand_rows}

    out = []
    for p in parts:
        cur = on_hand.get(p.id, 0)
        threshold = p.low_stock_report_quantity or 0
        if cur < threshold:
            out.append(
                {
                    "part_id": str(p.id),
                    "name": p.name,
                    "manufacturer": p.manufacturer,
                    "mpn": p.mpn,
                    "on_hand": cur,
                    "threshold": threshold,
                    "short_by": threshold - cur,
                }
            )
    out.sort(key=lambda r: r["short_by"], reverse=True)
    return ok(out)


@router.get("/bom-shortage")
def bom_shortage(
    db: DbSession,
    ws: CurrentWorkspace,
    project_id: UUID,
    quantity: int = Query(default=1, gt=0),
):
    """Project-wide shortage analysis at a given build quantity.
    Same engine as Build detail — no build is created."""
    project = db.get(Project, project_id)
    if not project or project.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="project not found")
    rows = shortage_analysis(db, workspace_id=ws.id, project=project, build_quantity=quantity)
    total_short = sum(r["short_by"] for r in rows)
    return ok({"project_id": str(project_id), "quantity": quantity, "rows": rows, "total_short": total_short})


@router.get("/stock-value")
def stock_value(db: DbSession, ws: CurrentWorkspace):
    """Sum of (lot.purchase_unit_cost × current_qty_in_lot) across all
    on-hand stock, broken down by currency. Untaged lots (no purchase
    cost) are listed under value 0."""
    # Per-lot current qty
    lot_rows = db.execute(
        select(StockEntry.lot_id, func.coalesce(func.sum(StockEntry.quantity_delta), 0))
        .where(StockEntry.workspace_id == ws.id)
        .where(StockEntry.status == "on_hand")
        .where(StockEntry.lot_id.is_not(None))
        .group_by(StockEntry.lot_id)
    ).all()
    lots = {l.id: l for l in db.query(Lot).filter(Lot.workspace_id == ws.id).all()}
    parts = {p.id: p for p in db.query(Part).filter(Part.workspace_id == ws.id).all()}

    by_currency: dict[str | None, float] = defaultdict(float)
    by_part: dict[UUID, dict] = {}
    for lot_id, qty in lot_rows:
        qty = int(qty)
        if qty <= 0:
            continue
        lot = lots.get(lot_id)
        if lot is None:
            continue
        unit_cost = float(lot.purchase_unit_cost) if lot.purchase_unit_cost is not None else 0.0
        currency = lot.purchase_currency
        value = unit_cost * qty
        by_currency[currency] += value
        bp = by_part.setdefault(
            lot.part_id,
            {
                "part_id": str(lot.part_id),
                "name": parts[lot.part_id].name if lot.part_id in parts else str(lot.part_id),
                "on_hand": 0,
                "value": 0.0,
                "currency": currency,
            },
        )
        bp["on_hand"] += qty
        bp["value"] += value
        # If multiple currencies on one part, leave as the first seen + flag
        if bp["currency"] and currency and bp["currency"] != currency:
            bp["currency"] = "MIXED"

    return ok(
        {
            "by_currency": [
                {"currency": cur, "value": round(v, 6)} for cur, v in sorted(by_currency.items(), key=lambda kv: (kv[0] or ""))
            ],
            "by_part": sorted(by_part.values(), key=lambda r: r["value"], reverse=True),
        }
    )


@router.get("/expiring-lots")
def expiring_lots(
    db: DbSession,
    ws: CurrentWorkspace,
    days: int = Query(default=90, ge=0, le=3650),
):
    """Lots expiring within the next `days` days (or already expired) that
    still have on-hand stock."""
    cutoff = date.today() + timedelta(days=days)
    lot_qty = {
        row[0]: int(row[1])
        for row in db.execute(
            select(StockEntry.lot_id, func.coalesce(func.sum(StockEntry.quantity_delta), 0))
            .where(StockEntry.workspace_id == ws.id)
            .where(StockEntry.status == "on_hand")
            .where(StockEntry.lot_id.is_not(None))
            .group_by(StockEntry.lot_id)
        ).all()
    }
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
        qty = lot_qty.get(l.id, 0)
        if qty <= 0:
            continue
        days_left = (l.expiration_date - today).days
        out.append(
            {
                "lot_id": str(l.id),
                "name": l.name,
                "part_id": str(l.part_id),
                "part_name": parts[l.part_id].name if l.part_id in parts else None,
                "on_hand": qty,
                "expiration_date": l.expiration_date.isoformat(),
                "days_until_expiry": days_left,
                "expired": days_left < 0,
            }
        )
    return ok(out)
