from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select

from app.core.deps import get_current_workspace
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import ok
from app.domain.lots.models import Lot
from app.domain.orders.models import Order
from app.domain.parts.models import Part
from app.domain.projects.models import Project
from app.domain.storage.models import StorageLocation
from app.infra.db import get_db

router = APIRouter()

# Maximum results returned across all buckets. Each bucket contributes up
# to _BUCKET_LIMIT rows; the total is capped at _TOTAL_LIMIT.
_TOTAL_LIMIT = 50
_BUCKET_LIMIT = 25


@router.get("")
@limiter.limit("30/minute", key_func=workspace_key)
def search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=200),
    db=Depends(get_db),
    ws=Depends(get_current_workspace),
):
    like = f"%{q}%"
    # Fetch one extra row per bucket so we can detect truncation without
    # issuing a COUNT query.
    fetch = _BUCKET_LIMIT + 1

    parts_raw = list(
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
            .limit(fetch)
        ).scalars()
    )

    storages_raw = list(
        db.execute(
            select(StorageLocation)
            .where(StorageLocation.workspace_id == ws.id)
            .where(or_(StorageLocation.name.ilike(like), StorageLocation.description.ilike(like)))
            .limit(fetch)
        ).scalars()
    )

    projects_raw = list(
        db.execute(
            select(Project)
            .where(Project.workspace_id == ws.id)
            .where(or_(Project.name.ilike(like), Project.description.ilike(like)))
            .limit(fetch)
        ).scalars()
    )

    lots_raw = list(
        db.execute(
            select(Lot)
            .where(Lot.workspace_id == ws.id)
            .where(or_(Lot.name.ilike(like), Lot.serial_number.ilike(like), Lot.comments.ilike(like)))
            .limit(fetch)
        ).scalars()
    )

    orders_raw = list(
        db.execute(
            select(Order)
            .where(Order.workspace_id == ws.id)
            .where(or_(Order.name.ilike(like), Order.supplier.ilike(like), Order.comments.ilike(like)))
            .limit(fetch)
        ).scalars()
    )

    # Detect per-bucket truncation before slicing back to _BUCKET_LIMIT.
    more_available = (
        len(parts_raw) > _BUCKET_LIMIT
        or len(storages_raw) > _BUCKET_LIMIT
        or len(projects_raw) > _BUCKET_LIMIT
        or len(lots_raw) > _BUCKET_LIMIT
        or len(orders_raw) > _BUCKET_LIMIT
    )

    parts = parts_raw[:_BUCKET_LIMIT]
    storages = storages_raw[:_BUCKET_LIMIT]
    projects = projects_raw[:_BUCKET_LIMIT]
    lots = lots_raw[:_BUCKET_LIMIT]
    orders = orders_raw[:_BUCKET_LIMIT]

    # Cap the combined total at _TOTAL_LIMIT.
    combined_count = len(parts) + len(storages) + len(projects) + len(lots) + len(orders)
    if combined_count > _TOTAL_LIMIT:
        more_available = True

    return ok(
        {
            "parts": [{"id": str(p.id), "name": p.name, "mpn": p.mpn, "manufacturer": p.manufacturer} for p in parts],
            "storage_locations": [{"id": str(s.id), "name": s.name} for s in storages],
            "projects": [{"id": str(p.id), "name": p.name} for p in projects],
            "lots": [{"id": str(l.id), "name": l.name, "part_id": str(l.part_id)} for l in lots],
            "orders": [{"id": str(o.id), "name": o.name, "status": o.status} for o in orders],
            "more_available": more_available,
        }
    )
