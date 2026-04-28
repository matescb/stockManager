from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.responses import ok
from app.domain.parts.models import Part, PartMetaMember, PartSubstitute
from app.domain.stock.service import (
    stock_summary_for_part,
    total_for_part,
)

router = APIRouter()


class PartIn(BaseModel):
    part_type: Literal["linked", "local", "meta", "sub_assembly"] = "local"
    name: str = Field(min_length=1, max_length=300)
    manufacturer: str | None = None
    mpn: str | None = None
    internal_part_number: str | None = None
    description: str | None = None
    notes_markdown: str | None = None
    footprint: str | None = None
    low_stock_report_quantity: int | None = None
    attrition_percentage: float = 0
    attrition_min_quantity: int = 0
    default_storage_location_id: UUID | None = None
    default_storage_mandatory: bool = False


class PartPatch(BaseModel):
    name: str | None = None
    manufacturer: str | None = None
    mpn: str | None = None
    internal_part_number: str | None = None
    description: str | None = None
    notes_markdown: str | None = None
    footprint: str | None = None
    low_stock_report_quantity: int | None = None
    attrition_percentage: float | None = None
    attrition_min_quantity: int | None = None
    default_storage_location_id: UUID | None = None
    default_storage_mandatory: bool | None = None


def _serialize(p: Part, *, on_hand: int | None = None) -> dict:
    return {
        "id": str(p.id),
        "part_type": p.part_type,
        "name": p.name,
        "manufacturer": p.manufacturer,
        "mpn": p.mpn,
        "internal_part_number": p.internal_part_number,
        "description": p.description,
        "footprint": p.footprint,
        "notes_markdown": p.notes_markdown,
        "low_stock_report_quantity": p.low_stock_report_quantity,
        "attrition_percentage": float(p.attrition_percentage or 0),
        "attrition_min_quantity": p.attrition_min_quantity or 0,
        "default_storage_location_id": str(p.default_storage_location_id) if p.default_storage_location_id else None,
        "default_storage_mandatory": p.default_storage_mandatory,
        "archived_at": p.archived_at.isoformat() if p.archived_at else None,
        "on_hand": on_hand,
    }


@router.get("")
def list_parts(
    db: DbSession,
    ws: CurrentWorkspace,
    q: str | None = Query(default=None),
    archived: bool = Query(default=False),
    mpn: str | None = Query(default=None),
    limit: int = Query(default=200, le=1000),
):
    stmt = select(Part).where(Part.workspace_id == ws.id)
    stmt = stmt.where(Part.archived_at.is_(None) if not archived else Part.archived_at.is_not(None))
    if mpn:
        stmt = stmt.where(Part.mpn == mpn)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Part.name.ilike(like),
                Part.mpn.ilike(like),
                Part.manufacturer.ilike(like),
                Part.internal_part_number.ilike(like),
                Part.description.ilike(like),
            )
        )
    stmt = stmt.order_by(Part.name).limit(limit)
    parts = list(db.execute(stmt).scalars())
    out = []
    for p in parts:
        on_hand = total_for_part(db, workspace_id=ws.id, part_id=p.id)
        out.append(_serialize(p, on_hand=on_hand))
    return ok(out)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_part(payload: PartIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    p = Part(
        workspace_id=ws.id,
        part_type=payload.part_type,
        name=payload.name,
        manufacturer=payload.manufacturer,
        mpn=payload.mpn,
        internal_part_number=payload.internal_part_number,
        description=payload.description,
        notes_markdown=payload.notes_markdown,
        footprint=payload.footprint,
        low_stock_report_quantity=payload.low_stock_report_quantity,
        attrition_percentage=payload.attrition_percentage,
        attrition_min_quantity=payload.attrition_min_quantity,
        default_storage_location_id=payload.default_storage_location_id,
        default_storage_mandatory=payload.default_storage_mandatory,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(p)
    db.commit()
    return ok(_serialize(p, on_hand=0))


def _get_part(db, ws_id, part_id) -> Part:
    p = db.get(Part, part_id)
    if not p or p.workspace_id != ws_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="part not found")
    return p


@router.get("/{part_id}")
def get_part(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id)
    on_hand = total_for_part(db, workspace_id=ws.id, part_id=p.id)
    return ok(_serialize(p, on_hand=on_hand))


@router.patch("/{part_id}")
def patch_part(part_id: UUID, payload: PartPatch, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    p = _get_part(db, ws.id, part_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    p.updated_by = user.id
    db.commit()
    return ok(_serialize(p, on_hand=total_for_part(db, workspace_id=ws.id, part_id=p.id)))


@router.post("/{part_id}/archive")
def archive_part(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    from datetime import datetime, timezone
    p = _get_part(db, ws.id, part_id)
    p.archived_at = datetime.now(timezone.utc)
    db.commit()
    return ok(None, "archived")


@router.post("/{part_id}/restore")
def restore_part(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id)
    p.archived_at = None
    db.commit()
    return ok(None, "restored")


@router.get("/{part_id}/stock")
def part_stock(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id)
    rows = stock_summary_for_part(db, workspace_id=ws.id, part_id=p.id)
    return ok(
        {
            "total_on_hand": total_for_part(db, workspace_id=ws.id, part_id=p.id),
            "rows": [
                {
                    "storage_location_id": str(r["storage_location_id"]) if r["storage_location_id"] else None,
                    "lot_id": str(r["lot_id"]) if r["lot_id"] else None,
                    "quantity": r["quantity"],
                }
                for r in rows
            ],
        }
    )


@router.get("/{part_id}/lots")
def part_lots(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    from app.domain.lots.models import Lot
    p = _get_part(db, ws.id, part_id)
    lots = list(
        db.execute(
            select(Lot).where(Lot.workspace_id == ws.id).where(Lot.part_id == p.id).order_by(Lot.created_at.desc())
        ).scalars()
    )
    return ok(
        [
            {
                "id": str(l.id),
                "name": l.name,
                "purchase_quantity": l.purchase_quantity,
                "purchase_unit_cost": float(l.purchase_unit_cost) if l.purchase_unit_cost is not None else None,
                "purchase_currency": l.purchase_currency,
                "expiration_date": l.expiration_date.isoformat() if l.expiration_date else None,
                "comments": l.comments,
                "parent_lot_id": str(l.parent_lot_id) if l.parent_lot_id else None,
                "source_type": l.source_type,
                "created_at": l.created_at.isoformat(),
            }
            for l in lots
        ]
    )


class SubstituteIn(BaseModel):
    substitute_part_id: UUID
    direction: Literal["one_way", "bidirectional"] = "bidirectional"


@router.post("/{part_id}/substitutes")
def add_substitute(part_id: UUID, payload: SubstituteIn, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id)
    sub = _get_part(db, ws.id, payload.substitute_part_id)
    db.add(PartSubstitute(part_id=p.id, substitute_part_id=sub.id, direction=payload.direction))
    db.commit()
    return ok(None)


@router.get("/{part_id}/substitutes")
def list_substitutes(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id)
    rows = list(db.execute(select(PartSubstitute).where(PartSubstitute.part_id == p.id)).scalars())
    return ok([{"part_id": str(r.substitute_part_id), "direction": r.direction} for r in rows])


@router.delete("/{part_id}/substitutes/{substitute_id}")
def del_substitute(part_id: UUID, substitute_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id)
    db.query(PartSubstitute).filter(
        PartSubstitute.part_id == p.id, PartSubstitute.substitute_part_id == substitute_id
    ).delete()
    db.commit()
    return ok(None)
