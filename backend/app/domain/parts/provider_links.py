"""Per-part provider links — which providers know this part, and as what.

One row per (part, provider). Written for the PRIMARY provider too, so
this table alone answers the question without the caller also having to
read `parts.linked_*`; those columns stay the primary's source of truth
for the part's own manufacturer / mpn / description.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.domain.parts.models import PartProviderLink

__all__ = [
    "delete_link",
    "get_link",
    "links_for_part",
    "serialize_link",
    "upsert_link",
]


def get_link(
    db: Session, *, workspace_id: UUID, part_id: UUID, provider: str
) -> PartProviderLink | None:
    return db.execute(
        select(PartProviderLink)
        .where(PartProviderLink.workspace_id == workspace_id)
        .where(PartProviderLink.part_id == part_id)
        .where(PartProviderLink.provider == provider)
        .where(PartProviderLink.archived_at.is_(None))
    ).scalars().first()


def links_for_part(
    db: Session, *, workspace_id: UUID, part_id: UUID
) -> list[PartProviderLink]:
    return list(
        db.execute(
            select(PartProviderLink)
            .where(PartProviderLink.workspace_id == workspace_id)
            .where(PartProviderLink.part_id == part_id)
            .where(PartProviderLink.archived_at.is_(None))
            .order_by(PartProviderLink.provider)
        ).scalars()
    )


def upsert_link(
    db: Session,
    *,
    workspace_id: UUID,
    part_id: UUID,
    user_id: UUID | None,
    provider: str,
    external_id: str | None = None,
    source_url: str | None = None,
    last_refresh_at: datetime | None = None,
) -> PartProviderLink:
    """Create or refresh the link row for (part, provider).

    A None `external_id` / `source_url` leaves the stored value alone —
    a lookup that came back without a product URL must not blank the one
    a previous refresh recorded. Caller owns the transaction.
    """
    row = get_link(db, workspace_id=workspace_id, part_id=part_id, provider=provider)
    if row is None:
        row = PartProviderLink(
            workspace_id=workspace_id,
            part_id=part_id,
            provider=provider,
            created_by=user_id,
        )
        db.add(row)

    if external_id is not None:
        row.external_id = external_id
    if source_url is not None:
        row.source_url = source_url
    row.last_refresh_at = last_refresh_at or utcnow()
    row.updated_by = user_id
    db.flush()
    return row


def delete_link(db: Session, row: PartProviderLink) -> None:
    """Hard-delete. The link carries no history worth a tombstone, and a
    soft-delete would have to dodge the partial unique index on every
    re-link."""
    db.delete(row)


def serialize_link(row: PartProviderLink) -> dict:
    return {
        "provider": row.provider,
        "external_id": row.external_id,
        "source_url": row.source_url,
        "last_refresh_at": (
            row.last_refresh_at.isoformat() if row.last_refresh_at else None
        ),
    }
