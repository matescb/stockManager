"""`export_parts` — the one MCP tool that walks the WHOLE inventory.

Every other listing in `read.py` is capped and says `truncated` when the
cap bites, because a model reading a hundred parts is a model doing its
job badly. An audit is the exception: "which parts have no 3D model" is
only answered by looking at every part, and no query an agent can write
is guaranteed to partition the set into pages that each fit under a cap
(SM-006). So this tool pages instead of truncating.

**Keyset on `Part.id`, nothing else.** The seek is `id > last_id` with
`ORDER BY id`. A name-ordered walk would read nicer but a rename between
two calls moves a part across the page boundary — served twice or never.
The id never changes, so a part inserted mid-walk lands either before
the cursor (missed, which is allowed) or after it (served once), and
nothing is ever served twice.

**A handful of queries per page, not per part.** One for the page, one
for the category index, two for EDA presence (the same helper
`find_parts_missing_eda` uses, so the two tools cannot disagree about
what "has a footprint" means), two for the content hashes, two for the
ledger roll-ups. The count does not grow with `page_size`.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, select

from app.core.errors import ErrorCodes
from app.core.pagination import Cursor, decode_cursor, encode_cursor
from app.domain._quantity import QUANTITY_ZERO, quantity_out
from app.domain.categories import service as categories_service
from app.domain.eda.models import (
    EdaDatafile,
    EdaFootprint,
    EdaFootprintModel,
    EdaSymbol,
    PartEda,
)
from app.domain.parts.models import Part
from app.domain.stock import service as stock_service
from app.mcp.principal import Caller
from app.mcp.tools._registry import ToolError, tool
from app.mcp.tools._shared import eda_status_for_parts, part_url, sid

# Default and ceiling for `page_size`. The ceiling matches
# `core/pagination.py::_MAX_PAGE_LIMIT`, the REST surface's cap on a
# cursor page. Over-asking is CLAMPED, like every other MCP `limit`,
# and the clamped value is echoed back as `page_size`.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 200

# Signed into every cursor this tool mints, and required on every
# cursor it accepts. A cursor from a REST listing is HMAC-valid too (same
# signer) but names a seek position in a different ordering; honouring
# it would silently skip parts.
_CURSOR_SCOPE = "mcp.export_parts"


@tool()
def export_parts(
    caller: Caller,
    cursor: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    include_archived: bool = False,
) -> dict[str, Any]:
    """Enumerate EVERY part in the workspace, page by page, for audits.

    Use this — not `search_parts`, `find_parts_missing_eda` or
    `stock_levels`, which are capped — when the question needs the whole
    inventory: "which parts lack a 3D model", "what is our stock of
    everything". Call it without `cursor`, then again with each
    `next_cursor` until `next_cursor` is null. Parts come in a stable
    order and none is ever returned twice; a part created during the
    walk may be missed.

    Args:
        cursor: The `next_cursor` from the previous page. Omit for the
            first page.
        page_size: Parts per page, 1-200 (default 100). Larger values
            are reduced to 200.
        include_archived: Also return archived parts (each then carries
            `archived: true`). Default false.

    Returns `items`, `next_cursor` (null on the last page) and the
    `page_size` actually used. Each item has `id`, `name`, `mpn`,
    `manufacturer`, `category` ({`id`, `name`, `path`} or null), `eda`
    with `symbol`, `footprint`, `model3d` and `spice`, each
    {`present`, `sha256`?, `ref_external`?} — `sha256` is the hosted
    file's content hash, absent when the slot points at the user's own
    local KiCad library (`ref_external`) — and `stock` with `on_hand`,
    `reserved` (committed to open builds) and `available`.
    """
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    after = _seek_position(cursor)

    stmt = select(Part).where(Part.workspace_id == caller.ws.id)
    if not include_archived:
        stmt = stmt.where(Part.archived_at.is_(None))
    if after is not None:
        stmt = stmt.where(Part.id > after)
    # One row over the page, so "is there another page" costs no COUNT.
    rows = list(
        caller.db.execute(stmt.order_by(Part.id).limit(page_size + 1)).scalars()
    )
    parts = rows[:page_size]
    next_cursor = (
        encode_cursor(Cursor(id=parts[-1].id, scope=_CURSOR_SCOPE))
        if len(rows) > page_size
        else None
    )
    return {
        "items": _items(caller, parts),
        "next_cursor": next_cursor,
        "page_size": page_size,
    }


def _seek_position(cursor: str | None) -> UUID | None:
    """The last id of the previous page, or a refusal.

    `decode_cursor` speaks HTTP (a 400 with a prose detail), which the
    registry would translate to a generic not-found. A bad cursor is a
    caller mistake with one fix — start over — so it gets its own code.
    """
    if cursor is None or cursor == "":
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
            "export_parts; call again without a cursor to start over"
        )
    return decoded.id


def _items(caller: Caller, parts: Sequence[Part]) -> list[dict[str, Any]]:
    if not parts:
        return []
    ids = [p.id for p in parts]
    index = categories_service.category_index(caller.db, ws_id=caller.ws.id)
    presence = eda_status_for_parts(caller, ids)
    hashes = _eda_hashes(caller, ids)
    on_hand = stock_service.bulk_current_quantities(
        caller.db, workspace_id=caller.ws.id, part_ids=ids
    )
    reserved = stock_service.bulk_current_quantities(
        caller.db, workspace_id=caller.ws.id, part_ids=ids, status="reserved"
    )

    items = []
    for part in parts:
        have = on_hand.get(part.id, QUANTITY_ZERO)
        held = reserved.get(part.id, QUANTITY_ZERO)
        item: dict[str, Any] = {
            "id": sid(part.id),
            "name": part.name,
            "mpn": part.mpn,
            "manufacturer": part.manufacturer,
            "category": _category(index, part.category_id),
            "eda": _eda(presence[part.id], hashes.get(part.id, {})),
            "stock": {
                "on_hand": quantity_out(have),
                "reserved": quantity_out(held),
                "available": quantity_out(have - held),
            },
            "part_url": part_url(part.id),
        }
        if part.archived_at is not None:
            item["archived"] = True
        items.append(item)
    return items


def _category(
    index: categories_service.CategoryIndex, category_id: UUID | None
) -> dict[str, Any] | None:
    # The index is workspace-scoped, so a foreign id (which the DB
    # trigger already refuses) would read as uncategorised, not leak.
    row = index.rows_by_id.get(category_id) if category_id is not None else None
    if row is None:
        return None
    return {"id": sid(row.id), "name": row.name, "path": index.paths.get(row.id)}


_SLOTS = (
    ("symbol", "has_symbol"),
    ("footprint", "has_footprint"),
    ("model3d", "has_model3d"),
    ("spice", "has_spice"),
)


def _eda(present: dict[str, bool], hashes: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for slot, flag in _SLOTS:
        entry: dict[str, Any] = {"present": bool(present[flag])}
        if present[flag] and hashes.get(slot):
            entry["sha256"] = hashes[slot]
        external = hashes.get(f"{slot}_ref_external")
        if present[flag] and external and "sha256" not in entry:
            entry["ref_external"] = external
        out[slot] = entry
    return out


def _eda_hashes(caller: Caller, part_ids: list[UUID]) -> dict[UUID, dict[str, str]]:
    """Content hashes of each part's hosted CAD files, in two queries.

    The outer joins each repeat the workspace check on the joined row —
    the FK alone does not prove the row is ours (CLAUDE.md). A 3D model
    hangs off the footprint and a footprint can carry several; the one
    reported is the first by `position`, which is the one KiCad places.
    """
    ws_id = caller.ws.id
    rows = caller.db.execute(
        select(
            PartEda.part_id,
            PartEda.footprint_id,
            PartEda.symbol_ref_external,
            PartEda.footprint_ref_external,
            EdaSymbol.sha256,
            EdaFootprint.sha256,
            EdaDatafile.sha256,
        )
        .outerjoin(
            EdaSymbol,
            and_(EdaSymbol.id == PartEda.symbol_id, EdaSymbol.workspace_id == ws_id),
        )
        .outerjoin(
            EdaFootprint,
            and_(
                EdaFootprint.id == PartEda.footprint_id,
                EdaFootprint.workspace_id == ws_id,
            ),
        )
        .outerjoin(
            EdaDatafile,
            and_(
                EdaDatafile.id == PartEda.spice_datafile_id,
                EdaDatafile.workspace_id == ws_id,
            ),
        )
        .where(PartEda.workspace_id == ws_id)
        .where(PartEda.part_id.in_(part_ids))
    ).all()

    model_sha = _first_model_hashes(
        caller, {row[1] for row in rows if row[1] is not None}
    )
    out: dict[UUID, dict[str, str]] = {}
    for part_id, fp_id, sym_ext, fp_ext, sym_sha, fp_sha, spice_sha in rows:
        out[part_id] = {
            k: v
            for k, v in {
                "symbol": sym_sha,
                "footprint": fp_sha,
                "spice": spice_sha,
                "model3d": model_sha.get(fp_id) if fp_id is not None else None,
                "symbol_ref_external": sym_ext,
                "footprint_ref_external": fp_ext,
            }.items()
            if v
        }
    return out


def _first_model_hashes(caller: Caller, footprint_ids: set[UUID]) -> dict[UUID, str]:
    if not footprint_ids:
        return {}
    ws_id = caller.ws.id
    rows = caller.db.execute(
        select(EdaFootprintModel.footprint_id, EdaDatafile.sha256)
        .join(
            EdaDatafile,
            and_(
                EdaDatafile.id == EdaFootprintModel.datafile_id,
                EdaDatafile.workspace_id == ws_id,
            ),
        )
        .where(EdaFootprintModel.workspace_id == ws_id)
        .where(EdaFootprintModel.footprint_id.in_(list(footprint_ids)))
        .order_by(
            EdaFootprintModel.footprint_id,
            EdaFootprintModel.position,
            EdaFootprintModel.id,
        )
    ).all()
    first: dict[UUID, str] = {}
    for fp_id, sha in rows:
        first.setdefault(fp_id, sha)
    return first
