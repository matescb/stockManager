"""Symbol-pin / footprint-pad consistency (SM-008): one part, or the audit.

Both tools are read-only and compute the verdict on demand from the
current files (`domain/eda/pin_pad_service.py`); nothing is stored. The
rules — exposed pads, thermal vias, mechanical pads, no-connect pins,
multi-unit symbols — are in `domain/eda/pin_pad.py`'s docstring.

`list_pin_pad_issues` pages exactly as `export_parts` does — keyset on
`Part.id`, a signed cursor whose scope names this tool — but filters
each page by verdict, so it scans up to `_SCAN_BUDGET` parts per call
and may return a short (even empty) page with a non-null `next_cursor`.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.core.errors import ErrorCodes
from app.core.pagination import Cursor, decode_cursor, encode_cursor
from app.domain.eda import pin_pad
from app.domain.eda.models import PartEda
from app.domain.eda.pin_pad_service import check_parts
from app.domain.parts.models import Part
from app.mcp.principal import Caller
from app.mcp.tools._registry import ToolError, tool
from app.mcp.tools._shared import part_url, resolve_part, sid

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
# Parts examined per call, whatever their verdict. Bounds one call's
# file reads; the cursor resumes where the scan stopped.
_SCAN_BUDGET = 500
_CHUNK = 100
# Distinct from `mcp.export_parts`: a cursor from one walk names a seek
# position in the other's filter and must not be honoured.
_CURSOR_SCOPE = "mcp.list_pin_pad_issues"

_WANTED = {
    "fail": {pin_pad.FAIL},
    "not-checkable": {pin_pad.NOT_CHECKABLE},
    "all": {pin_pad.FAIL, pin_pad.NOT_CHECKABLE},
}


@tool()
def check_pin_pad(caller: Caller, part_id_or_mpn: str) -> dict[str, Any]:
    """Check that a part's symbol pins and footprint pads match by number.

    Every symbol pin number needs a footprint pad of the same number,
    and every electrical pad needs a pin. Computed now from the current
    files; nothing is stored or changed.

    Args:
        part_id_or_mpn: The part's id or exact MPN.

    Returns `verdict` — `pass`, `fail`, or `not-checkable` with a
    `reason` (`symbol_missing`, `footprint_missing`, `symbol_external` /
    `footprint_external` for an entry in the user's local KiCad library,
    `symbol_unreadable`, `footprint_unreadable`, `symbol_derived`,
    `body_styles_differ`) and `detail`; `symbol_sha256`,
    `footprint_sha256`, `symbol_ref`, `footprint_ref`, `checked_at`; and
    `findings` with `pins_without_pad`, `pads_without_pin`,
    `duplicate_pin_numbers` (each fails the check) and
    `no_connect_pins_without_pad` (information only). Legal and never a
    failure: unnumbered pads, NPTH / mechanical pads, paste- or
    mask-only pads, and several pads sharing one number (exposed pad,
    thermal vias). Multi-unit symbols are checked as the union of their
    units' pins.
    """
    part = resolve_part(caller, part_id_or_mpn)
    return {
        "part_id": sid(part.id),
        "part_url": part_url(part.id),
        **check_parts(caller.db, caller.ws.id, [part])[part.id],
    }


@tool()
def list_pin_pad_issues(
    caller: Caller,
    verdict: Literal["fail", "not-checkable", "all"] = "all",
    cursor: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """Audit the whole workspace for symbol-pin / footprint-pad mismatches.

    Walks every active part that has a symbol or a footprint configured
    (parts with neither are `find_parts_missing_eda`'s work) and returns
    the ones whose `check_pin_pad` verdict matches. Call without
    `cursor`, then again with each `next_cursor` until it is null. A
    page can hold fewer than `page_size` items — even none — while
    `next_cursor` is still set: keep going.

    Args:
        verdict: `fail`, `not-checkable` (one side missing, in the
            user's local library, or unreadable), or `all` (both;
            default). Passing parts are never listed.
        cursor: The `next_cursor` from the previous page.
        page_size: Items per page, 1-100 (default 50); larger is reduced.

    Returns `items` (each: `part_id`, `name`, `mpn`, `part_url` and the
    `check_pin_pad` fields), `next_cursor`, `page_size` and `scanned`.
    """
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    after = _seek_position(cursor)
    wanted = _WANTED[verdict]
    items: list[dict[str, Any]] = []
    scanned = 0
    while True:
        rows = _candidates(caller, after, _CHUNK + 1)
        chunk, more = rows[:_CHUNK], len(rows) > _CHUNK
        checks = check_parts(caller.db, caller.ws.id, chunk)
        for index, part in enumerate(chunk):
            after, scanned = part.id, scanned + 1
            if checks[part.id]["verdict"] in wanted:
                items.append(_item(part, checks[part.id]))
            if len(items) >= page_size or scanned >= _SCAN_BUDGET:
                remaining = more or index < len(chunk) - 1
                return _page(items, after if remaining else None, page_size, scanned)
        if not more:
            return _page(items, None, page_size, scanned)


def _candidates(caller: Caller, after: UUID | None, limit: int) -> list[Part]:
    """Active parts whose own EDA config names a symbol or a footprint.

    A category default alone does not qualify: every part of a category
    with `Device:R` as its default would list as `symbol_external`."""
    ws_id = caller.ws.id
    stmt = (
        select(Part)
        .join(
            PartEda,
            (PartEda.part_id == Part.id) & (PartEda.workspace_id == ws_id),
        )
        .where(Part.workspace_id == ws_id)
        .where(Part.archived_at.is_(None))
        .where(
            or_(
                PartEda.symbol_id.is_not(None),
                PartEda.symbol_ref_external.is_not(None),
                PartEda.footprint_id.is_not(None),
                PartEda.footprint_ref_external.is_not(None),
            )
        )
    )
    if after is not None:
        stmt = stmt.where(Part.id > after)
    return list(caller.db.execute(stmt.order_by(Part.id).limit(limit)).scalars())


def _item(part: Part, check: dict[str, Any]) -> dict[str, Any]:
    return {
        "part_id": sid(part.id),
        "name": part.name,
        "mpn": part.mpn,
        "part_url": part_url(part.id),
        **check,
    }


def _page(
    items: list[dict[str, Any]], last: UUID | None, page_size: int, scanned: int
) -> dict[str, Any]:
    return {
        "items": items,
        "next_cursor": (
            encode_cursor(Cursor(id=last, scope=_CURSOR_SCOPE)) if last else None
        ),
        "page_size": page_size,
        "scanned": scanned,
    }


def _seek_position(cursor: str | None) -> UUID | None:
    if not cursor:
        return None
    try:
        decoded = decode_cursor(cursor)
    except HTTPException:
        decoded = None
    if (
        decoded is None
        or decoded.scope != _CURSOR_SCOPE
        or decoded.sort_key is not None
        or decoded.sort_keys is not None
    ):
        raise ToolError(
            f"{ErrorCodes.MCP_INVALID_CURSOR}: cursor was not issued by "
            "list_pin_pad_issues; call again without a cursor to start over"
        )
    return decoded.id
