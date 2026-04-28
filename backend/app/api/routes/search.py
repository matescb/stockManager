from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select

from app.core.deps import get_current_workspace
from app.core.responses import ok
from app.domain.lots.models import Lot
from app.domain.orders.models import Order
from app.domain.parts.models import Part
from app.domain.projects.models import Project
from app.domain.storage.models import StorageLocation
from app.infra.db import get_db

router = APIRouter()


@router.get("")
def search(q: str = Query(..., min_length=1), db=Depends(get_db), ws=Depends(get_current_workspace)):
    like = f"%{q}%"

    parts = list(
        db.execute(
            select(Part)
            .where(Part.workspace_id == ws.id)
            .where(
                or_(
                    Part.name.ilike(like),
                    Part.mpn.ilike(like),
                    Part.manufacturer.ilike(like),
                    Part.internal_part_number.ilike(like),
                    Part.description.ilike(like),
                )
            )
            .limit(25)
        ).scalars()
    )

    storages = list(
        db.execute(
            select(StorageLocation)
            .where(StorageLocation.workspace_id == ws.id)
            .where(or_(StorageLocation.name.ilike(like), StorageLocation.description.ilike(like)))
            .limit(15)
        ).scalars()
    )

    projects = list(
        db.execute(
            select(Project)
            .where(Project.workspace_id == ws.id)
            .where(or_(Project.name.ilike(like), Project.description.ilike(like)))
            .limit(15)
        ).scalars()
    )

    lots = list(
        db.execute(
            select(Lot)
            .where(Lot.workspace_id == ws.id)
            .where(or_(Lot.name.ilike(like), Lot.serial_number.ilike(like), Lot.comments.ilike(like)))
            .limit(15)
        ).scalars()
    )

    orders = list(
        db.execute(
            select(Order)
            .where(Order.workspace_id == ws.id)
            .where(or_(Order.name.ilike(like), Order.supplier.ilike(like), Order.comments.ilike(like)))
            .limit(15)
        ).scalars()
    )

    return ok(
        {
            "parts": [{"id": str(p.id), "name": p.name, "mpn": p.mpn, "manufacturer": p.manufacturer} for p in parts],
            "storage_locations": [{"id": str(s.id), "name": s.name} for s in storages],
            "projects": [{"id": str(p.id), "name": p.name} for p in projects],
            "lots": [{"id": str(l.id), "name": l.name, "part_id": str(l.part_id)} for l in lots],
            "orders": [{"id": str(o.id), "name": o.name, "status": o.status} for o in orders],
        }
    )
