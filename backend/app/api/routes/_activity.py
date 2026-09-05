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

Cursor pagination
-----------------
All three activity routes support `(occurred_at DESC, id DESC)` cursor
pagination via query params `before_occurred_at` + `before_id`.

  ?before_occurred_at=<isoformat>&before_id=<uuid>&limit=<int>

The response body includes `next_before_occurred_at` / `next_before_id`
when a subsequent page exists; omitting these keys signals the last page.

User-map caching
----------------
`_user_map` accepts an optional ``cache`` dict (typically
``request.state.user_cache``) and populates it lazily — the user table is
queried at most once per unique user_id per request across all calls that
share the same cache dict.
"""

from __future__ import annotations

from typing import Iterable
from uuid import UUID

from fastapi import Request, status
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.errors import ErrorCodes, raise_http
from app.domain._quantity import quantity_out
from app.domain.stock.models import StockEntry
from app.domain.users.models import User

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def parse_activity_cursor(value: str | None):
    if value is None:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCodes.ACTIVITY_INVALID_CURSOR,
            message="invalid before_occurred_at",
        )


def activity_stock_rows(db: Session, stmt, *, cursor_at, before_id, limit: int):
    if cursor_at is not None and before_id is not None:
        stmt = stmt.where(
            or_(
                StockEntry.occurred_at < cursor_at,
                and_(StockEntry.occurred_at == cursor_at, StockEntry.id < before_id),
            )
        )
    stmt = stmt.order_by(StockEntry.occurred_at.desc(), StockEntry.id.desc()).limit(limit + 1)
    return list(db.execute(stmt).scalars())


def request_user_cache(request: Request):
    if not hasattr(request.state, "user_cache"):
        request.state.user_cache = {}
    return request.state.user_cache


def route_activity(
    request: Request,
    db: Session,
    stmt,
    *,
    before_occurred_at: str | None,
    before_id,
    limit: int,
    entity,
    created_kind: str,
    updated_kind: str,
):
    cursor_at = parse_activity_cursor(before_occurred_at)
    stock_rows = activity_stock_rows(
        db, stmt, cursor_at=cursor_at, before_id=before_id, limit=limit
    )
    return build_activity(
        db,
        stock_rows=stock_rows,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        created_by=entity.created_by,
        updated_by=entity.updated_by,
        created_kind=created_kind,
        updated_kind=updated_kind,
        limit=limit,
        include_synthetic=(cursor_at is None),
        user_cache=request_user_cache(request),
    )


def _user_dict(user: User | None) -> dict | None:
    if user is None:
        return None
    return {"id": str(user.id), "name": user.name}


def _user_map(
    db: Session,
    user_ids: Iterable[UUID | None],
    *,
    cache: dict[UUID, User] | None = None,
) -> dict[UUID, User]:
    """Return a mapping of user_id → User for the given ids.

    When ``cache`` is supplied (e.g. ``request.state.user_cache``) it is
    used as a read-through cache: only ids that are absent from the cache
    trigger a DB query; results are written back so subsequent calls within
    the same request share the warm dict.
    """
    ids = {uid for uid in user_ids if uid is not None}
    if not ids:
        return {}

    if cache is None:
        # No cache — fetch all ids in one shot.
        rows = db.query(User).filter(User.id.in_(ids)).all()
        return {u.id: u for u in rows}

    # Cache-aware path: resolve the miss-set, query only those, merge.
    missing = ids - cache.keys()
    if missing:
        rows = db.query(User).filter(User.id.in_(missing)).all()
        for u in rows:
            cache[u.id] = u

    return {uid: cache[uid] for uid in ids if uid in cache}


def _stock_event(row: StockEntry, user: User | None) -> dict:
    return {
        "kind": "stock",
        "id": str(row.id),
        "operation_type": row.operation_type,
        "quantity_delta": quantity_out(row.quantity_delta),
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
        "id": None,
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
    limit: int = _DEFAULT_LIMIT,
    include_synthetic: bool = True,
    user_cache: dict[UUID, User] | None = None,
) -> dict:
    """Combine entity create/update synthetic events with ``stock_rows``.

    Returns a dict with:
      - ``events``               — list of activity dicts, sorted occurred_at DESC
      - ``next_before_occurred_at`` — isoformat string, present when next page exists
      - ``next_before_id``          — UUID string, present when next page exists

    Parameters
    ----------
    stock_rows:
        Pre-fetched stock entries for this page (already ordered + cursor-filtered
        by the caller, length <= limit + 1 to detect next-page existence).
    limit:
        Maximum number of events to include in this page (default 50, max 200).
    include_synthetic:
        When False the created/updated synthetic events are omitted — callers set
        this to False on continuation pages so they only appear on the first page.
    user_cache:
        Optional per-request dict shared across activity helper calls to avoid
        re-querying the users table per route invocation.
    """
    limit = min(max(1, limit), _MAX_LIMIT)

    # Detect whether there's a next page: callers fetch limit+1 rows.
    has_next = len(stock_rows) > limit
    page_rows = stock_rows[:limit]

    user_ids: list[UUID | None] = [created_by, updated_by]
    user_ids.extend(r.created_by for r in page_rows)
    users = _user_map(db, user_ids, cache=user_cache)

    events: list[dict] = []
    if include_synthetic:
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

    for row in page_rows:
        events.append(_stock_event(row, users.get(row.created_by) if row.created_by else None))

    events.sort(key=lambda e: e["occurred_at"], reverse=True)

    result: dict = {"events": events}

    if has_next and page_rows:
        # Cursor points at the last stock row returned (oldest on this page).
        last = page_rows[-1]
        result["next_before_occurred_at"] = last.occurred_at.isoformat()
        result["next_before_id"] = str(last.id)

    return result
