from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.parts.models import Part

UQ_PARTS_WS_MPN = "uq_parts_ws_mpn"


def active_part_by_mpn(db, *, workspace_id: UUID, mpn: str | None) -> Part | None:
    if not mpn:
        return None
    return (
        db.execute(
            select(Part)
            .where(Part.workspace_id == workspace_id)
            .where(Part.archived_at.is_(None))
            .where(Part.mpn == mpn)
            .limit(1)
        )
        .scalars()
        .first()
    )


def is_mpn_unique_violation(exc: IntegrityError) -> bool:
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None) == UQ_PARTS_WS_MPN
