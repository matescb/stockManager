"""Shared helpers for entity activity timelines.

The activity endpoints surface a chronological union of:
  - a synthetic "<entity>_created" event (from the row's created_at/created_by)
  - a synthetic "<entity>_updated" event when updated_at != created_at
  - all stock_entries rows linked to that entity (via part_id / order_id /
    build_id), reusing each row's `operation_type`

Output is a flat union shape — see /docs/spec or the route docstrings for the
exact JSON. We keep it a single shape (kind discriminator + nullable fields)
because every consumer just renders a list of entries; carving out separate
shapes per kind would force the frontend to handle three branches for what
is fundamentally one timeline.
"""

from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.stock.models import StockEntry
from app.domain.users.models import User


_LIMIT = 200


def _user_dict(user: User | None) -> dict | None:
    if user is None:
        return None
    return {"id": str(user.id), "name": user.name}


def _user_map(db: Session, user_ids: Iterable[UUID | None]) -> dict[UUID, User]:
    ids = {uid for uid in user_ids if uid is not None}
    if not ids:
        return {}
    rows = db.query(User).filter(User.id.in_(ids)).all()
    return {u.id: u for u in rows}


def _stock_event(row: StockEntry, user: User | None) -> dict:
    return {
        "kind": "stock",
        "operation_type": row.operation_type,
        "quantity_delta": row.quantity_delta,
        "user": _user_dict(user),
        "occurred_at": row.occurred_at.isoformat(),
        "comments": row.comments,
        "lot_id": str(row.lot_id) if row.lot_id else None,
        "storage_location_id": (
            str(row.storage_location_id) if row.storage_location_id else None
        ),
        "order_id": str(row.order_id) if row.order_id else None,
        "build_id": str(row.build_id) if row.build_id else None,
    }


def _entity_event(
    *,
    kind: str,
    occurred_at,
    user: User | None,
) -> dict:
    return {
        "kind": kind,
        "operation_type": None,
        "quantity_delta": None,
        "user": _user_dict(user),
        "occurred_at": occurred_at.isoformat(),
        "comments": None,
        "lot_id": None,
        "storage_location_id": None,
        "order_id": None,
        "build_id": None,
    }


def build_activity(
    db: Session,
    *,
    stock_rows: list[StockEntry],
    created_at,
    updated_at,
    created_by: UUID | None,
    updated_by: UUID | None,
    created_kind: str,
    updated_kind: str,
) -> list[dict]:
    """Combine entity create/update synthetic events with `stock_rows` and
    return them sorted by occurred_at DESC, capped at 200."""
    user_ids: list[UUID | None] = [created_by, updated_by]
    user_ids.extend(r.created_by for r in stock_rows)
    users = _user_map(db, user_ids)

    events: list[dict] = []
    if created_at is not None:
        events.append(
            _entity_event(
                kind=created_kind,
                occurred_at=created_at,
                user=users.get(created_by) if created_by else None,
            )
        )
    if updated_at is not None and updated_at != created_at:
        events.append(
            _entity_event(
                kind=updated_kind,
                occurred_at=updated_at,
                user=users.get(updated_by) if updated_by else None,
            )
        )
    for row in stock_rows:
        events.append(_stock_event(row, users.get(row.created_by) if row.created_by else None))

    events.sort(key=lambda e: e["occurred_at"], reverse=True)
    return events[:_LIMIT]
