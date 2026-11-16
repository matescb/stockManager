"""Box intake and box audit (phase 15).

Two scan-driven workflows on one storage location ("box"):

- **Intake** — every scanned bag lands in the box as its own lot, so the
  bag can be found and counted again later. A part that is not in the
  library yet is created from the workspace's primary provider, exactly
  as bulk-import-from-scan does. A bag already recorded somewhere else is
  moved here rather than added twice.
- **Audit** — the box's recorded contents (``box_bags``) are checked bag
  by bag; the operator's verdict per bucket (counted / write off / move)
  is applied in one transaction by ``apply_audit``.

Every quantity write goes through ``domain/stock/service.py``. The two
read-only GROUP BYs here (``box_bags``, ``_bag_locations``) have the same
shape as ``service.stock_for_storage``.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ErrorCodes
from app.domain._quantity import QUANTITY_ZERO, as_quantity, quantity_out
from app.domain.lots.models import Lot
from app.domain.parts.models import Part
from app.domain.parts.services.bag_signature import compute_bag_signature
from app.domain.parts.services.mpn_unique import active_part_by_mpn, is_mpn_unique_violation
from app.domain.parts.services.provider_import import create_from_provider_lookup
from app.domain.stock.models import StockEntry
from app.domain.stock.schemas import (
    AddStockIn,
    AdjustStockIn,
    BoxAuditIn,
    BoxIntakeIn,
    BoxScanResolveIn,
    LotInput,
)
from app.domain.stock.service import (
    StockError,
    add_stock,
    adjust_stock,
    current_quantity,
    move_quantity,
)
from app.domain.storage.models import StorageLocation

# `lookup(mpn)` returns the provider_cache result dict ({found, result, ...}).
# Callers pass None when the workspace has no provider configured.
Lookup = Callable[[str], dict]

INTAKE_COMMENT = "box intake"
AUDIT_COMMENT = "box audit"


class BoxScanError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400, **extra) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.extra = extra


@dataclass(frozen=True)
class KnownBag:
    part_id: UUID
    lot_id: UUID | None


def _part_summary(p: Part | None) -> dict | None:
    if p is None:
        return None
    return {
        "id": str(p.id),
        "name": p.name,
        "mpn": p.mpn,
        "manufacturer": p.manufacturer,
        "description": p.description,
    }


def _usable_part(db: Session, workspace_id: UUID, part_id: UUID | None) -> Part | None:
    p = db.get(Part, part_id) if part_id else None
    if p is None or p.workspace_id != workspace_id or p.archived_at is not None:
        return None
    return p


def _verify_signature(signature: str | None, raw: str | None) -> None:
    if signature and raw is not None and compute_bag_signature(raw) != signature:
        raise BoxScanError(
            ErrorCodes.STOCK_BAG_SIGNATURE_MISMATCH,
            "bag_signature does not match recomputed digest of raw_bag_code",
            status=422,
        )


def find_bag(db: Session, *, workspace_id: UUID, signature: str | None) -> KnownBag | None:
    """The most recent stock entry stamped with this bag signature."""
    if not signature:
        return None
    row = db.execute(
        select(StockEntry.part_id, StockEntry.lot_id)
        .where(StockEntry.workspace_id == workspace_id)
        .where(StockEntry.bag_signature == signature)
        .where(StockEntry.part_id.is_not(None))
        .order_by(StockEntry.occurred_at.desc())
        .limit(1)
    ).first()
    return KnownBag(part_id=row[0], lot_id=row[1]) if row else None


def _bag_locations(
    db: Session, *, workspace_id: UUID, lot_id: UUID
) -> dict[UUID | None, Decimal]:
    """Non-zero on-hand quantity of one lot, per storage location."""
    rows = db.execute(
        select(
            StockEntry.storage_location_id,
            func.coalesce(func.sum(StockEntry.quantity_delta), 0),
        )
        .where(StockEntry.workspace_id == workspace_id)
        .where(StockEntry.lot_id == lot_id)
        .where(StockEntry.status == "on_hand")
        .group_by(StockEntry.storage_location_id)
    ).all()
    return {sid: q for sid, raw in rows if (q := as_quantity(raw)) != QUANTITY_ZERO}


def _location_names(db: Session, *, workspace_id: UUID, ids) -> dict[UUID, str]:
    ids = [i for i in ids if i is not None]
    if not ids:
        return {}
    rows = db.execute(
        select(StorageLocation.id, StorageLocation.name)
        .where(StorageLocation.workspace_id == workspace_id)
        .where(StorageLocation.id.in_(ids))
    ).all()
    return dict(rows)


def _bag_info(db: Session, *, workspace_id: UUID, lot_id: UUID, here: UUID) -> dict | None:
    locs = _bag_locations(db, workspace_id=workspace_id, lot_id=lot_id)
    if not locs:
        return None
    names = _location_names(db, workspace_id=workspace_id, ids=locs.keys())
    lot = db.get(Lot, lot_id)
    return {
        "lot_id": str(lot_id),
        "lot_name": lot.name if lot else None,
        "here": here in locs,
        "quantity_here": quantity_out(locs.get(here, QUANTITY_ZERO)),
        "locations": [
            {
                "storage_location_id": str(sid) if sid else None,
                "name": names.get(sid) if sid else None,
                "quantity": quantity_out(q),
            }
            for sid, q in locs.items()
        ],
    }


def _resolved(status: str, *, part=None, bag=None, provider=None) -> dict:
    return {"status": status, "part": _part_summary(part), "bag": bag, "provider": provider}


def resolve_scan(
    db: Session,
    *,
    workspace_id: UUID,
    storage: StorageLocation,
    payload: BoxScanResolveIn,
    lookup: Lookup | None,
) -> dict:
    """What a scan refers to, relative to this box. Read-only.

    status: bag_here | bag_elsewhere | existing_part | provider_match | not_found.
    A bag whose stock is all gone, or that predates per-bag lots, resolves
    as ``existing_part`` — intake then records it as a new bag.
    """
    _verify_signature(payload.bag_signature, payload.raw_bag_code)
    known = find_bag(db, workspace_id=workspace_id, signature=payload.bag_signature)
    if known is not None:
        part = _usable_part(db, workspace_id, known.part_id)
        if part is not None:
            info = (
                _bag_info(db, workspace_id=workspace_id, lot_id=known.lot_id, here=storage.id)
                if known.lot_id
                else None
            )
            if info is None:
                return _resolved("existing_part", part=part)
            return _resolved("bag_here" if info["here"] else "bag_elsewhere", part=part, bag=info)

    mpn = (payload.mpn or "").strip()
    if not mpn:
        return _resolved("not_found")
    part = active_part_by_mpn(db, workspace_id=workspace_id, mpn=mpn)
    if part is not None:
        return _resolved("existing_part", part=part)
    if lookup is not None and payload.provider_lookup:
        out = lookup(mpn)
        if out and out.get("found") and out.get("result"):
            r = out["result"]
            return _resolved(
                "provider_match",
                provider={
                    "mpn": r.get("mpn") or mpn,
                    "manufacturer": r.get("manufacturer"),
                    "description": r.get("description"),
                    "image_url": r.get("image_url"),
                },
            )
    return _resolved("not_found")


def _create_from_provider(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    mpn: str,
    lookup: Lookup | None,
    provider_name: str | None,
) -> tuple[Part, bool]:
    if lookup is None or provider_name is None:
        raise BoxScanError(
            ErrorCodes.BOX_SCAN_PART_NOT_FOUND,
            f"{mpn} is not in the library and no parts provider is configured",
            status=404,
            mpn=mpn,
        )
    out = lookup(mpn)
    if not out or not out.get("found") or not out.get("result"):
        raise BoxScanError(
            ErrorCodes.BOX_SCAN_PART_NOT_FOUND,
            f"{mpn} is not in the library and the provider has no match",
            status=404,
            mpn=mpn,
        )
    try:
        with db.begin_nested():
            outcome = create_from_provider_lookup(
                db,
                workspace_id=workspace_id,
                user_id=user_id,
                provider_name=provider_name,
                mpn=mpn,
                lookup_result=out["result"],
            )
    except IntegrityError as exc:
        # Lost a race with another scan of the same MPN.
        existing = (
            active_part_by_mpn(db, workspace_id=workspace_id, mpn=mpn)
            if is_mpn_unique_violation(exc)
            else None
        )
        if existing is None:
            raise
        return existing, False
    return outcome.part, True


def _part_for_intake(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    payload: BoxIntakeIn,
    known: KnownBag | None,
    lookup: Lookup | None,
    provider_name: str | None,
) -> tuple[Part, bool]:
    """(part, created) — explicit id, then the bag's part, then MPN, then provider."""
    if payload.part_id is not None:
        p = _usable_part(db, workspace_id, payload.part_id)
        if p is None:
            raise BoxScanError(ErrorCodes.PART_NOT_FOUND, "part not found", status=404)
        return p, False
    if known is not None and (p := _usable_part(db, workspace_id, known.part_id)):
        return p, False
    mpn = (payload.mpn or "").strip()
    if not mpn:
        raise BoxScanError(ErrorCodes.BOX_SCAN_NO_PART, "part_id or mpn is required", status=422)
    p = active_part_by_mpn(db, workspace_id=workspace_id, mpn=mpn)
    if p is not None:
        return p, False
    return _create_from_provider(
        db, workspace_id=workspace_id, user_id=user_id, mpn=mpn,
        lookup=lookup, provider_name=provider_name,
    )


def _move_known_bag_here(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    storage: StorageLocation,
    known: KnownBag,
    quantity: int,
) -> dict | None:
    """Relocate every remaining unit of a known bag into the box and set its
    count. None when the bag has nothing left anywhere (treated as new)."""
    part = _usable_part(db, workspace_id, known.part_id)
    locs = _bag_locations(db, workspace_id=workspace_id, lot_id=known.lot_id)
    if part is None or not locs:
        return None
    moved = []
    for sid, qty in locs.items():
        if sid == storage.id or qty <= 0:
            continue
        move_quantity(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            part_id=part.id,
            quantity=qty,
            source_storage_location_id=sid,
            source_lot_id=known.lot_id,
            destination_storage_location_id=storage.id,
            comments=INTAKE_COMMENT,
        )
        moved.append({"storage_location_id": str(sid) if sid else None,
                      "quantity": quantity_out(qty)})
    adjust_stock(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=AdjustStockIn(
            part_id=part.id,
            storage_location_id=storage.id,
            lot_id=known.lot_id,
            actual_quantity=quantity,
            comments=INTAKE_COMMENT,
        ),
    )
    return {
        "status": "moved" if moved else "already_here",
        "part": _part_summary(part),
        "part_created": False,
        "lot_id": str(known.lot_id),
        "quantity": quantity,
        "moved_from": moved,
    }


def intake_bag(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    storage: StorageLocation,
    payload: BoxIntakeIn,
    lookup: Lookup | None,
    provider_name: str | None,
) -> dict:
    """Put one bag into the box. Re-submitting the same bag is a no-op
    (status ``already_here``) apart from correcting its count."""
    _verify_signature(payload.bag_signature, payload.raw_bag_code)
    if storage.archived_at is not None:
        raise BoxScanError(ErrorCodes.STORAGE_ARCHIVED, "storage location is archived")
    known = find_bag(db, workspace_id=workspace_id, signature=payload.bag_signature)

    if known is not None and known.lot_id is not None and payload.on_existing == "move":
        result = _move_known_bag_here(
            db, workspace_id=workspace_id, user_id=user_id, storage=storage,
            known=known, quantity=payload.quantity,
        )
        if result is not None:
            return result

    part, created = _part_for_intake(
        db, workspace_id=workspace_id, user_id=user_id, payload=payload,
        known=known, lookup=lookup, provider_name=provider_name,
    )
    lot_name = (payload.lot_name or "").strip() or f"Bag {date.today().isoformat()}"
    entry = add_stock(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        payload=AddStockIn(
            part_id=part.id,
            quantity=payload.quantity,
            storage_location_id=storage.id,
            lot=LotInput(name=lot_name, serial_number=payload.lot_serial or None),
            comments=payload.comments or INTAKE_COMMENT,
            bag_signature=payload.bag_signature,
        ),
    )
    return {
        "status": "added",
        "part": _part_summary(part),
        "part_created": created,
        "lot_id": str(entry.lot_id),
        "quantity": payload.quantity,
        "moved_from": [],
    }


def _bucket_rows(db: Session, *, workspace_id: UUID, storage_location_id: UUID):
    rows = db.execute(
        select(
            StockEntry.part_id,
            StockEntry.lot_id,
            func.coalesce(func.sum(StockEntry.quantity_delta), 0),
        )
        .where(StockEntry.workspace_id == workspace_id)
        .where(StockEntry.storage_location_id == storage_location_id)
        .where(StockEntry.status == "on_hand")
        .where(StockEntry.part_id.is_not(None))
        .group_by(StockEntry.part_id, StockEntry.lot_id)
    ).all()
    return [(pid, lid, q) for pid, lid, raw in rows if (q := as_quantity(raw)) != QUANTITY_ZERO]


def box_bags(db: Session, *, workspace_id: UUID, storage_location_id: UUID) -> list[dict]:
    """The box's recorded contents: one row per non-empty (part, lot) bucket.
    ``lot`` is null for stock recorded without a lot (not trackable per bag)."""
    buckets = _bucket_rows(db, workspace_id=workspace_id, storage_location_id=storage_location_id)
    if not buckets:
        return []
    part_ids = {b[0] for b in buckets}
    lot_ids = {b[1] for b in buckets if b[1] is not None}
    parts = {
        p.id: p
        for p in db.execute(
            select(Part).where(Part.workspace_id == workspace_id).where(Part.id.in_(part_ids))
        ).scalars()
    }
    lots: dict[UUID, Lot] = {}
    sigs: dict[UUID, list[str]] = defaultdict(list)
    if lot_ids:
        lots = {
            lot.id: lot
            for lot in db.execute(
                select(Lot).where(Lot.workspace_id == workspace_id).where(Lot.id.in_(lot_ids))
            ).scalars()
        }
        for lid, sig in db.execute(
            select(StockEntry.lot_id, StockEntry.bag_signature)
            .where(StockEntry.workspace_id == workspace_id)
            .where(StockEntry.lot_id.in_(lot_ids))
            .where(StockEntry.bag_signature.is_not(None))
            .distinct()
        ).all():
            sigs[lid].append(sig)
    out = []
    for pid, lid, qty in buckets:
        lot = lots.get(lid) if lid else None
        out.append({
            "part": _part_summary(parts.get(pid)),
            "lot": {"id": str(lot.id), "name": lot.name} if lot else None,
            "quantity": quantity_out(qty),
            "bag_signatures": sorted(sigs.get(lid, [])) if lid else [],
        })
    out.sort(key=lambda r: (
        (r["part"] or {}).get("name") or "",
        (r["lot"] or {}).get("name") or "",
    ))
    return out


def _apply_audit_move(db, *, workspace_id, user_id, storage, line, comment) -> bool:
    if line.to_storage_location_id is None:
        raise StockError("to_storage_location_id is required for move")
    if line.to_storage_location_id == storage.id:
        raise StockError("cannot move a bag into the box it is already in")
    qty = current_quantity(
        db, workspace_id=workspace_id, part_id=line.part_id,
        storage_location_id=storage.id, lot_id=line.lot_id, bucket_match=True,
    )
    if qty <= 0:
        return False
    move_quantity(
        db, workspace_id=workspace_id, user_id=user_id, part_id=line.part_id,
        quantity=qty, source_storage_location_id=storage.id, source_lot_id=line.lot_id,
        destination_storage_location_id=line.to_storage_location_id, comments=comment,
    )
    return True


def apply_audit(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    storage: StorageLocation,
    payload: BoxAuditIn,
) -> dict:
    """Apply the operator's verdict on each bucket of the box. All or
    nothing: a StockError on any line raises, and the route rolls back."""
    counts = {"count": 0, "write_off": 0, "move": 0, "unchanged": 0}
    comment = payload.comments or AUDIT_COMMENT
    for idx, line in enumerate(payload.lines):
        try:
            if line.action == "move":
                changed = _apply_audit_move(
                    db, workspace_id=workspace_id, user_id=user_id,
                    storage=storage, line=line, comment=comment,
                )
                counts["move" if changed else "unchanged"] += 1
                continue
            if line.action == "count" and line.actual_quantity is None:
                raise StockError("actual_quantity is required for count")
            entry = adjust_stock(
                db,
                workspace_id=workspace_id,
                user_id=user_id,
                payload=AdjustStockIn(
                    part_id=line.part_id,
                    storage_location_id=storage.id,
                    lot_id=line.lot_id,
                    actual_quantity=0 if line.action == "write_off" else line.actual_quantity,
                    comments=comment,
                ),
            )
            counts[line.action if entry is not None else "unchanged"] += 1
        except StockError as exc:
            raise BoxScanError(
                ErrorCodes.STOCK_OPERATION_ERROR, f"line {idx + 1}: {exc}", line=idx
            ) from exc
    return counts
