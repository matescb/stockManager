"""Cursor-based pagination primitives (BE2-025 / issue #69).

Provides:
  - Cursor dataclass (id, sort_key, sort_keys)
  - encode_cursor / decode_cursor — HMAC-signed base64 so tampered cursors
    return 400 rather than leaking or crashing.
  - paginate() — appends a tuple-aware WHERE (sort_key, id) > (?, ?) clause
    with ORDER BY sort_key, id tiebreaker and enforces a sane page limit.
  - paginate_keyset() — the same seek, over N **nullable** sort expressions
    with NULLS LAST, for sorts whose key is not a column of the selected
    entity (the parts list's per-category spec columns join
    `custom_fields` and order by `(value_num, value)`).

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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import and_, false, or_
from sqlalchemy.orm import Session

from app.core.config import settings

# Maximum rows per page for cursor-aware endpoints.
_MAX_PAGE_LIMIT = 200
_DEFAULT_PAGE_LIMIT = 50


@dataclass
class Cursor:
    id: UUID
    sort_key: str | None = None  # None when sorting by id only; ISO-8601 string for datetime cols
    #: Seek position for a `paginate_keyset` sort — one string (or None,
    #: for a SQL NULL) per sort expression, in ORDER BY order. Mutually
    #: exclusive with `sort_key`: a cursor carries one shape or the other,
    #: and handing the wrong one to either paginator is a 400 rather than a
    #: silent restart from page 1.
    sort_keys: tuple[str | None, ...] | None = None


def _signer() -> URLSafeSerializer:
    return URLSafeSerializer(settings().SESSION_SECRET, salt="cursor-v1")


def encode_cursor(c: Cursor) -> str:
    """Sign and base64-encode a cursor so the client can hand it back."""
    payload: dict[str, Any] = {"id": str(c.id)}
    if c.sort_key is not None:
        payload["sk"] = c.sort_key
    if c.sort_keys is not None:
        # A list, not a tuple: the payload is JSON-encoded by itsdangerous
        # and a tuple would come back as a list anyway. Encoding it as one
        # keeps `dumps(loads(s)) == s` true, which `decode_cursor` relies
        # on for its canonical-form check.
        payload["sks"] = list(c.sort_keys)
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
        raw_keys = payload.get("sks")
        if raw_keys is not None and not isinstance(raw_keys, list):
            raise ValueError("sks must be a list")
        return Cursor(
            id=UUID(payload["id"]),
            sort_key=payload.get("sk"),
            sort_keys=None if raw_keys is None else tuple(raw_keys),
        )
    except (KeyError, TypeError, ValueError):
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
    _refuse_wrong_cursor_shape(cursor, wants_composite=False)

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


# ---------------------------------------------------------------------------
# Multi-column keyset pagination over NULLABLE sort expressions.
#
# `paginate()` above seeks on one NOT NULL column of the selected entity,
# and says so: `sort_col > NULL` is unknown in SQL, so a nullable sort
# column would silently skip every NULL row on page two onwards. Sorting
# the parts list by a per-category spec column breaks both assumptions at
# once — the sort key is `custom_fields.value_num` / `.value` on an OUTER
# JOINed row, so it is nullable by construction (a part with no such spec
# has no row at all), and it takes two expressions to order one column
# (the parsed number first so `10 kΩ` sorts before `100 kΩ`, the display
# text second so an unparsed value still sorts alphabetically instead of
# collapsing into one undifferentiated NULL group).
#
# So: N expressions, NULLS LAST on every one of them, `id` ascending as the
# final tiebreaker, and a seek predicate that spells the NULL cases out
# instead of relying on comparison operators that cannot express them.
# ---------------------------------------------------------------------------


def _refuse_wrong_cursor_shape(cursor: Cursor | None, *, wants_composite: bool) -> None:
    """400 when a cursor's seek shape doesn't match the requested sort.

    A `paginate()` cursor carries `sort_key`; a `paginate_keyset()` cursor
    carries `sort_keys`. Feeding one to the other happens when a client
    keeps paging after changing the sort, and the tempting behaviour —
    ignore the cursor — silently restarts at page one *while the client
    appends the rows to what it already has*, so the user sees the first
    page twice and never reaches the end. Failing is the honest answer;
    the client drops the cursor and refetches.
    """
    if cursor is None:
        return
    has_composite = cursor.sort_keys is not None
    if has_composite is not wants_composite:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cursor does not match the requested sort",
        )


def _decoded_seek(
    raw: tuple[str | None, ...],
    decoders: Sequence[Callable[[str], Any]] | None,
    width: int,
) -> tuple[Any, ...]:
    """Cursor strings -> bind values, or 400.

    The cursor is HMAC-signed, so a wrong width or an unparseable value
    means the client is replaying a cursor minted for a different sort —
    not tampering. Either way it cannot be honoured, and a `Decimal("x")`
    blowing up in the query builder would be a 500.
    """
    if len(raw) != width:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cursor does not match the requested sort",
        )
    out: list[Any] = []
    for index, item in enumerate(raw):
        if item is None:
            out.append(None)
            continue
        decode = decoders[index] if decoders is not None else str
        try:
            out.append(decode(item))
        except (ArithmeticError, TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="malformed cursor payload",
            )
    return tuple(out)


def _strictly_after(expr, value: Any, asc: bool):
    """`expr` is past `value` in NULLS-LAST order.

    A NULL `value` means the cursor row sat in the trailing NULL group, and
    with NULLS LAST nothing sorts after a NULL at that level — the rows
    that follow are the ones the *next* level separates, which the equality
    leg of the composite predicate covers. Hence `false` rather than
    `expr IS NOT NULL`, which would wrongly re-serve every non-NULL row.
    """
    if value is None:
        return false()
    return or_(expr > value if asc else expr < value, expr.is_(None))


def _same_as(expr, value: Any):
    return expr.is_(None) if value is None else expr == value


def paginate_keyset(
    db: Session,
    stmt,
    *,
    sort_exprs: Sequence[Any],
    id_col,
    cursor: Cursor | None,
    limit: int,
    asc: bool = True,
    decoders: Sequence[Callable[[str], Any]] | None = None,
) -> tuple[list, str | None]:
    """Execute a keyset-paginated query sorted by `sort_exprs`, NULLS LAST.

    Parameters
    ----------
    stmt:        A ``select(Entity)`` whose workspace filter the caller has
                 already applied (CLAUDE.md invariant), with any JOIN the
                 sort expressions need already on it.
    sort_exprs:  SQL expressions to ORDER BY, most significant first. They
                 do NOT have to be columns of the selected entity — that is
                 the whole point — so they are re-selected internally to
                 build the next cursor rather than read off the row object.
    id_col:      Unique tiebreaker. Always ascending, whatever `asc` says,
                 so a page boundary lands in the same place both ways.
    decoders:    Per-expression `str -> bind value`, for columns whose
                 Python type is not `str` (`Decimal` for `NUMERIC`). One
                 entry per sort expression; defaults to `str` throughout.

    Returns `(entities, next_cursor_str | None)` — same contract as
    `paginate()`, so the two are interchangeable at the call site.
    """
    limit = min(max(int(limit), 1), _MAX_PAGE_LIMIT)
    sort_exprs = tuple(sort_exprs)
    _refuse_wrong_cursor_shape(cursor, wants_composite=True)

    if cursor is not None and cursor.sort_keys is not None:
        seek = _decoded_seek(cursor.sort_keys, decoders, len(sort_exprs))
        # after(c1) OR (c1 = s1 AND after(c2)) OR … OR (all equal AND id > s_id)
        legs = []
        prefix: list[Any] = []
        for expr, value in zip(sort_exprs, seek):
            legs.append(and_(*prefix, _strictly_after(expr, value, asc)))
            prefix.append(_same_as(expr, value))
        legs.append(and_(*prefix, id_col > cursor.id))
        stmt = stmt.where(or_(*legs))

    ordering = [
        (expr.asc() if asc else expr.desc()).nullslast() for expr in sort_exprs
    ]
    stmt = stmt.order_by(*ordering, id_col.asc())

    # Re-select the sort expressions alongside the entity: they may live on
    # an OUTER JOINed table, so the returned object cannot answer "what was
    # this row's sort value" and the next cursor has nowhere else to come
    # from. Every expression is single-valued per entity row here (the
    # parts-list join is on `uq_cf_unique`), so this adds no fan-out.
    rows = db.execute(stmt.add_columns(*sort_exprs).limit(limit + 1)).all()

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    entities = [row[0] for row in rows]
    next_cursor: str | None = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(
            Cursor(
                id=getattr(last[0], id_col.key),
                sort_keys=tuple(
                    None if value is None else str(value) for value in last[1:]
                ),
            )
        )
    return entities, next_cursor
