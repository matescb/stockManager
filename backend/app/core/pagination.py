"""Cursor-based pagination primitives (BE2-025 / issue #69).

Provides:
  - Cursor dataclass (id, sort_key)
  - encode_cursor / decode_cursor — HMAC-signed base64 so tampered cursors
    return 400 rather than leaking or crashing.
  - paginate() — appends a tuple-aware WHERE (sort_key, id) > (?, ?) clause
    with ORDER BY sort_key, id tiebreaker and enforces a sane page limit.

Design notes:
  - itsdangerous.URLSafeSerializer is used for signing. The key is
    settings().SESSION_SECRET, which is already in config.py and well-known
    to the process.
  - sort_key can be a str, datetime, or None. Datetimes are serialised as
    ISO-8601 strings (itsdangerous JSON-encodes the payload).
  - paginate() is intentionally generic — it takes a SQLAlchemy Select
    statement and returns (rows, next_cursor_str | None). The caller owns
    workspace_id filtering: per CLAUDE.md every query must filter by ws.id.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.config import settings

# Maximum rows per page for cursor-aware endpoints.
_MAX_PAGE_LIMIT = 200
_DEFAULT_PAGE_LIMIT = 50


@dataclass
class Cursor:
    id: UUID
    sort_key: str | None  # None when sorting by id only; ISO-8601 string for datetime cols


def _signer() -> URLSafeSerializer:
    return URLSafeSerializer(settings().SESSION_SECRET, salt="cursor-v1")


def encode_cursor(c: Cursor) -> str:
    """Sign and base64-encode a cursor so the client can hand it back."""
    payload: dict[str, Any] = {"id": str(c.id)}
    if c.sort_key is not None:
        payload["sk"] = c.sort_key
    return _signer().dumps(payload)


def decode_cursor(s: str) -> Cursor:
    """Decode and verify a cursor.  Raises HTTP 400 on tamper / malform."""
    try:
        signer = _signer()
        payload: dict[str, Any] = signer.loads(s)
        if not hmac.compare_digest(s, signer.dumps(payload)):
            raise BadSignature("non-canonical cursor token")
    except BadSignature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or tampered cursor",
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="malformed cursor",
        )
    try:
        return Cursor(
            id=UUID(payload["id"]),
            sort_key=payload.get("sk"),
        )
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="malformed cursor payload",
        )


def paginate(
    db: Session,
    stmt,
    *,
    sort_col,
    id_col,
    cursor: Cursor | None,
    limit: int,
    asc: bool = True,
) -> tuple[list, str | None]:
    """Execute a cursor-paginated query and return (rows, next_cursor).

    Parameters
    ----------
    db:        SQLAlchemy session.
    stmt:      A ``select(Model)`` statement.  Must already have
               workspace_id filter applied by the caller (CLAUDE.md
               invariant).
    sort_col:  The ORM column to sort on (e.g. Part.name).
    id_col:    The unique-tiebreaker column (e.g. Part.id).
    cursor:    Decoded cursor from the previous page, or None for first page.
    limit:     Desired page size; clamped to [1, _MAX_PAGE_LIMIT].
    asc:       True for ascending order (default).

    Returns
    -------
    (rows, next_cursor_str)
        rows           — the page's items (plain ORM objects).
        next_cursor_str — opaque string to pass as ``?cursor=`` on the
                          next request; None when this is the last page.
    """
    limit = min(max(int(limit), 1), _MAX_PAGE_LIMIT)

    # Apply cursor filter — tuple comparison gives correct pagination
    # semantics without a separate "seek" / double-query technique.
    #
    # NULL caveat: `sort_col > NULL` is unknown (i.e. false) in SQL, so if
    # `sort_col` is nullable AND a cursor row's sort_key is NULL, ANY row
    # with a NULL sort_col would be silently skipped on subsequent pages.
    # All current callers (Part.name) sort on a NOT NULL column so this is
    # safe today; new callers must either use a NOT NULL column or extend
    # this helper with explicit NULLS FIRST/LAST handling before plugging
    # in a nullable sort_col.
    if cursor is not None:
        cursor_sort_val: Any = cursor.sort_key
        # Re-inflate datetime sort keys that were serialised as ISO strings.
        if cursor_sort_val is not None:
            try:
                sort_col_type = sort_col.property.columns[0].type
                if hasattr(sort_col_type, "impl") and hasattr(sort_col_type.impl, "python_type"):
                    py_type = sort_col_type.impl.python_type
                elif hasattr(sort_col_type, "python_type"):
                    py_type = sort_col_type.python_type
                else:
                    py_type = str
                if py_type is datetime:
                    cursor_sort_val = datetime.fromisoformat(cursor.sort_key)
            except Exception:
                pass  # leave as string — DB will cast or error

        cursor_id = cursor.id
        if asc:
            # (sort_key, id) > (cursor_sort_val, cursor_id)
            stmt = stmt.where(
                or_(
                    sort_col > cursor_sort_val,
                    and_(sort_col == cursor_sort_val, id_col > cursor_id),
                )
            )
        else:
            stmt = stmt.where(
                or_(
                    sort_col < cursor_sort_val,
                    and_(sort_col == cursor_sort_val, id_col < cursor_id),
                )
            )

    # ORDER BY — always include id as tiebreaker so pagination is stable
    # even when sort_col has duplicates (e.g. many parts share a name prefix).
    if asc:
        stmt = stmt.order_by(sort_col.asc(), id_col.asc())
    else:
        stmt = stmt.order_by(sort_col.desc(), id_col.desc())

    # Fetch one extra row to detect whether a next page exists without
    # a separate COUNT query.
    rows = list(db.execute(stmt.limit(limit + 1)).scalars())

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    next_cursor: str | None = None
    if has_more and rows:
        last = rows[-1]
        raw_sort = getattr(last, sort_col.key)
        if isinstance(raw_sort, datetime):
            sk = raw_sort.isoformat()
        elif raw_sort is None:
            sk = None
        else:
            sk = str(raw_sort)
        next_cursor = encode_cursor(Cursor(id=getattr(last, id_col.key), sort_key=sk))

    return rows, next_cursor
