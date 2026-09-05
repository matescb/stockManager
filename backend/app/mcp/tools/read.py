"""Read-only MCP tools: parts, stock, storage, categories, projects.

Every docstring in this module is a prompt. It is what a language model
reads to decide whether to call the tool and what to pass it, and it is
the only documentation it will ever see — so each one says what the tool
answers, what the arguments mean in the user's vocabulary, and what the
result looks like. That is a different job from explaining the code, and
the two are kept apart: implementation notes live in comments.
"""
from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import or_, select

from app.domain._quantity import quantity_out
from app.domain.builds import service as builds_service
from app.domain.categories import service as categories_service
from app.domain.eda.models import PartEda
from app.domain.parts.models import Part
from app.domain.projects.models import Project, ProjectEntry
from app.domain.stock import service as stock_service
from app.domain.storage.models import StorageLocation
from app.mcp.principal import Caller
from app.mcp.tools._registry import tool
from app.mcp.tools._shared import (
    compact,
    custom_fields_for,
    eda_status,
    eda_status_for_parts,
    part_eda_payload,
    part_summary,
    part_url,
    resolve_category,
    resolve_part,
    sid,
)

# Ceilings on every listing. An agent that asks for 10,000 parts is
# going to spend its context window on the answer and get worse at the
# task, so the cap is low on purpose and the result says when it bit.
_SEARCH_LIMIT = 50
_MISSING_EDA_LIMIT = 100
_STOCK_LIMIT = 200

# How many parts `find_parts_missing_eda` will look AT, as opposed to
# how many it will return. The two are different numbers because the
# filter runs in Python: a workspace whose parts all have footprints
# yields nothing and would otherwise still read the whole table.
_SCAN_CAP = 2000


@tool()
def search_parts(
    caller: Caller,
    query: str,
    category_slug: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Find parts in the inventory by free-text search.

    Searches name, manufacturer part number (MPN), manufacturer,
    internal part number and description, case-insensitively, for parts
    that are not archived.

    Args:
        query: What to look for, e.g. "10k 0603" or "STM32G0".
        category_slug: Optional category to restrict the search to, as
            returned by `list_categories`.
        limit: Maximum number of parts to return (1-50).

    Returns a `parts` list of summaries and a `truncated` flag that is
    true when more parts matched than were returned — narrow the query
    rather than raising the limit.
    """
    limit = max(1, min(limit, _SEARCH_LIMIT))
    stmt = (
        select(Part)
        .where(Part.workspace_id == caller.ws.id)
        .where(Part.archived_at.is_(None))
    )
    if category_slug:
        stmt = stmt.where(Part.category_id == resolve_category(caller, category_slug).id)
    term = f"%{_escape_like(query.strip())}%"
    stmt = stmt.where(
        or_(
            Part.name.ilike(term, escape="\\"),
            Part.mpn.ilike(term, escape="\\"),
            Part.manufacturer.ilike(term, escape="\\"),
            Part.internal_part_number.ilike(term, escape="\\"),
            Part.description.ilike(term, escape="\\"),
        )
    ).order_by(Part.name, Part.id)

    # One row over the limit, so "there are more" is answered without a
    # second COUNT query over the same predicate.
    rows = list(caller.db.execute(stmt.limit(limit + 1)).scalars())
    return {
        "parts": [part_summary(p) for p in rows[:limit]],
        "truncated": len(rows) > limit,
    }


def _escape_like(value: str) -> str:
    r"""Neutralise LIKE wildcards in user text.

    `%` and `_` are wildcards to `ILIKE`, so an agent searching for the
    literal string "10%" was matching every part whose name starts with
    "10" — quietly wrong answers rather than an error, which is the
    worse failure. The backslash itself goes first, or escaping the
    others would double-escape it.
    """
    return (
        value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


@tool()
def get_part(caller: Caller, id_or_mpn: str) -> dict[str, Any]:
    """Everything known about one part: specs, stock, storage and CAD status.

    Args:
        id_or_mpn: Either the part's id (as returned by `search_parts`)
            or its exact manufacturer part number.

    Returns the part's identity and description, its `specs`
    (user-curated attributes such as tolerance or package),
    `catalog_fields` (distributor metadata such as image and datasheet
    URLs), `stock` (`on_hand`, `reserved`, `available`, and a
    `locations` breakdown by storage location name), `eda` (whether a
    schematic symbol, PCB footprint, 3D model and SPICE model are
    configured), and `part_url`.
    """
    part = resolve_part(caller, id_or_mpn)
    catalog, specs = custom_fields_for(caller, part)

    on_hand = stock_service.current_quantity(
        caller.db, workspace_id=caller.ws.id, part_id=part.id
    )
    reserved = stock_service.reserved_quantity(
        caller.db, workspace_id=caller.ws.id, part_id=part.id
    )
    summary = stock_service.stock_summary_for_part(
        caller.db, workspace_id=caller.ws.id, part_id=part.id
    )
    names = _storage_names(caller)

    return compact(
        {
            **part_summary(part),
            "part_type": part.part_type,
            "footprint": part.footprint,
            "specs": specs,
            "catalog_fields": catalog,
            "stock": {
                "on_hand": on_hand,
                "reserved": reserved,
                "available": on_hand - reserved,
                "locations": [
                    {
                        "storage_location_id": sid(row["storage_location_id"]),
                        "storage_location_name": names.get(row["storage_location_id"]),
                        "quantity": row["quantity"],
                    }
                    for row in summary
                ],
            },
            "eda": eda_status(caller, part),
            "low_stock_report_quantity": quantity_out(part.low_stock_report_quantity),
        }
    )


def _storage_names(caller: Caller) -> dict:
    """id → name for every storage location in the workspace.

    One query for the whole workspace rather than one per ledger bucket:
    a part spread over a dozen bins would otherwise be a dozen round
    trips to render one result.
    """
    rows = caller.db.execute(
        select(StorageLocation.id, StorageLocation.name).where(
            StorageLocation.workspace_id == caller.ws.id
        )
    ).all()
    return {row[0]: row[1] for row in rows}


@tool()
def get_part_eda(caller: Caller, part_id: str) -> dict[str, Any]:
    """The CAD (KiCad) configuration for one part.

    Args:
        part_id: The part's id or exact MPN.

    Returns which schematic symbol, PCB footprint and SPICE model the
    part uses. Hosted library entries are reported with both their id
    and the KiCad reference string (`PCM_SM_<category>:<Entry>`) that
    will appear in a schematic; entries the user keeps in their own
    local libraries are reported as `symbol_ref_external` /
    `footprint_ref_external`. `configured: false` means the part has no
    CAD configuration at all yet.
    """
    part = resolve_part(caller, part_id)
    config = (
        caller.db.execute(
            select(PartEda)
            .where(PartEda.workspace_id == caller.ws.id)
            .where(PartEda.part_id == part.id)
        )
        .scalars()
        .first()
    )
    return part_eda_payload(caller, part, config)


@tool()
def find_parts_missing_eda(
    caller: Caller,
    kind: Literal["symbol", "footprint", "model3d", "spice"],
    category_slug: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List parts that have no CAD data of a given kind.

    The tool for "what still needs a footprint?" — the usual starting
    point for a library-maintenance session.

    Args:
        kind: Which kind of CAD data is missing. `symbol` and
            `footprint` count a reference to the user's own local
            library as present. `model3d` asks whether the part's
            footprint has a 3D model attached, so a part with no
            footprint at all is also missing its model.
        category_slug: Optional category to restrict the answer to.
        limit: Maximum number of parts to return (1-100).

    Returns a `parts` list of summaries and a `truncated` flag.
    """
    limit = max(1, min(limit, _MISSING_EDA_LIMIT))
    stmt = (
        select(Part)
        .where(Part.workspace_id == caller.ws.id)
        .where(Part.archived_at.is_(None))
    )
    if category_slug:
        stmt = stmt.where(Part.category_id == resolve_category(caller, category_slug).id)
    stmt = stmt.order_by(Part.name, Part.id)

    # Filtered in Python against `eda_status_for_parts`, not in SQL. The
    # predicate for "has a symbol" spans two nullable columns on a table
    # that may have no row at all, and `model3d` needs a further hop
    # through the footprint's join table — expressing that as one query
    # means an outer-join chain whose emptiness rules would then have to
    # be kept in step with `eda_status` by hand, and the two disagreeing
    # is worse than this being a scan.
    #
    # So the SCAN is what has to be bounded, not just the result. A
    # workspace where every part already has a footprint would otherwise
    # load the entire parts table to return zero rows — the cap on
    # `limit` bounds the answer, which is a different thing. Reading
    # `_SCAN_CAP` rows and reporting `truncated` when the cap is reached
    # keeps the cost of this tool flat in the size of the library.
    scanned = list(caller.db.execute(stmt.limit(_SCAN_CAP + 1)).scalars())
    scan_truncated = len(scanned) > _SCAN_CAP
    scanned = scanned[:_SCAN_CAP]

    status = eda_status_for_parts(caller, [p.id for p in scanned])
    wanted = f"has_{kind}"
    found: list[dict[str, Any]] = []
    truncated = scan_truncated
    for part in scanned:
        if status[part.id][wanted]:
            continue
        if len(found) == limit:
            truncated = True
            break
        found.append(part_summary(part))
    return {"parts": found, "truncated": truncated, "scanned": len(scanned)}


@tool()
def stock_levels(
    caller: Caller,
    part_id: str | None = None,
    low_stock_only: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    """Current stock for one part, or across the whole inventory.

    Args:
        part_id: A part id or exact MPN to report on. Omit to report on
            every part in the workspace.
        low_stock_only: When true, return only parts at or below their
            configured low-stock threshold. Parts with no threshold set
            are never included.
        limit: Maximum number of parts to report on when `part_id` is
            omitted (1-200).

    Returns a `parts` list, each with `on_hand`, `reserved` (committed
    to open builds) and `available` (`on_hand` minus `reserved` — the
    number that matters before promising a build), plus a `truncated`
    flag when the workspace has more parts than were reported.
    """
    truncated = False
    if part_id is not None:
        parts = [resolve_part(caller, part_id)]
    else:
        limit = max(1, min(limit, _STOCK_LIMIT))
        # Bounded, because this is the tool an agent calls to "check
        # stock" with no arguments at all. Unbounded it loaded every
        # part in the workspace and then asked one aggregate query per
        # row for the reserved figure — the whole table, N+1 times over.
        rows_ = list(
            caller.db.execute(
                select(Part)
                .where(Part.workspace_id == caller.ws.id)
                .where(Part.archived_at.is_(None))
                .order_by(Part.name, Part.id)
                .limit(limit + 1)
            ).scalars()
        )
        truncated = len(rows_) > limit
        parts = rows_[:limit]

    # Two grouped queries for the whole page rather than two per part.
    # `reserved` is the same ledger sum as `on_hand` with a different
    # `status`, so the batched helper serves both — which also means
    # they can't drift apart in how they define the total.
    ids = [p.id for p in parts]
    on_hand = stock_service.bulk_current_quantities(
        caller.db, workspace_id=caller.ws.id, part_ids=ids
    )
    reserved_by_part = stock_service.bulk_current_quantities(
        caller.db, workspace_id=caller.ws.id, part_ids=ids, status="reserved"
    )

    rows = []
    for part in parts:
        have = on_hand.get(part.id, 0)
        reserved = reserved_by_part.get(part.id, 0)
        threshold = quantity_out(part.low_stock_report_quantity)
        if low_stock_only and (threshold is None or have > threshold):
            continue
        rows.append(
            compact(
                {
                    **part_summary(part),
                    "on_hand": have,
                    "reserved": reserved,
                    "available": have - reserved,
                    "low_stock_threshold": threshold,
                }
            )
        )
    return {"parts": rows, "truncated": truncated}


@tool()
def list_storage_locations(caller: Caller) -> dict[str, Any]:
    """Every storage location (bin, drawer, shelf) in the workspace.

    Returns each location's id and name, plus `is_full` and the
    `single_part_only` / `existing_parts_only` flags that constrain what
    may be put there — `add_stock` and `move_stock` will refuse a
    destination that violates one.
    """
    rows = caller.db.execute(
        select(StorageLocation)
        .where(StorageLocation.workspace_id == caller.ws.id)
        .where(StorageLocation.archived_at.is_(None))
        .order_by(StorageLocation.name)
    ).scalars()
    return {
        "storage_locations": [
            compact(
                {
                    "id": sid(row.id),
                    "name": row.name,
                    "description": row.description,
                    "is_full": row.is_full,
                    "single_part_only": row.single_part_only,
                    "existing_parts_only": row.existing_parts_only,
                }
            )
            for row in rows
        ]
    }


@tool()
def list_categories(caller: Caller) -> dict[str, Any]:
    """Every part category in the workspace.

    Returns each category's id, name and `slug`. The slug is what other
    tools take as `category_slug`; it also names the KiCad symbol
    library the category's parts are published into.
    """
    rows = categories_service.list_categories(caller.db, ws=caller.ws)
    return {
        "categories": [
            compact(
                {
                    "id": sid(row.id),
                    "name": row.name,
                    "slug": row.library_slug,
                    "description": row.description,
                    "refdes_prefix": row.refdes_prefix,
                }
            )
            for row in rows
        ]
    }


@tool()
def list_projects(caller: Caller) -> dict[str, Any]:
    """Every project (board / product) in the workspace, newest first.

    Returns each project's id, name and description. Use
    `get_project_bom` for a project's bill of materials.
    """
    rows = caller.db.execute(
        select(Project)
        .where(Project.workspace_id == caller.ws.id)
        .where(Project.archived_at.is_(None))
        .order_by(Project.updated_at.desc())
    ).scalars()
    return {
        "projects": [
            compact(
                {
                    "id": sid(row.id),
                    "name": row.name,
                    "description": row.description,
                }
            )
            for row in rows
        ]
    }


def _project(caller: Caller, project_id: str) -> Project:
    from uuid import UUID

    from fastapi import status

    from app.core.errors import ErrorCodes, raise_http

    try:
        parsed = UUID(project_id)
    except ValueError:
        parsed = None
    row = None if parsed is None else caller.db.get(Project, parsed)
    if row is None or row.workspace_id != caller.ws.id:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.PROJECT_NOT_FOUND,
            f"no project {project_id!r} in this workspace",
        )
    return row


@tool()
def get_project_bom(caller: Caller, project_id: str) -> dict[str, Any]:
    """The bill of materials for one project.

    Args:
        project_id: The project's id, from `list_projects`.

    Returns the BOM `lines` in order, each with its `designators`
    (e.g. "R1,R4"), `quantity` per board, the linked part where one is
    linked, and `dnp` (do not populate) where set. A line with no
    `part_id` is not linked to inventory yet.
    """
    project = _project(caller, project_id)
    rows = caller.db.execute(
        select(ProjectEntry)
        .where(ProjectEntry.workspace_id == caller.ws.id)
        .where(ProjectEntry.project_id == project.id)
        .order_by(ProjectEntry.order_index, ProjectEntry.id)
    ).scalars()
    return {
        "project_id": sid(project.id),
        "project_name": project.name,
        "lines": [
            compact(
                {
                    "id": sid(row.id),
                    "entry_type": row.entry_type,
                    "name": row.name,
                    "part_id": sid(row.part_id),
                    "part_url": part_url(row.part_id) if row.part_id else None,
                    "quantity": quantity_out(row.quantity),
                    "designators": row.designators,
                    "cad_footprint": row.cad_footprint,
                    "dnp": row.dnp,
                    "comments": row.comments,
                }
            )
            for row in rows
        ],
    }


@tool()
def bom_shortages(caller: Caller, project_id: str, build_qty: int = 1) -> dict[str, Any]:
    """What you are short of to build a project, and by how much.

    Args:
        project_id: The project's id, from `list_projects`.
        build_qty: How many boards you intend to build.

    Returns one row per BOM line that cannot be fully covered from
    available stock, with `required`, `available` and `short_by`.
    Substitutes and meta-part members count towards availability, so a
    line backed by an approved alternative is not reported short.
    Do-not-populate lines and lines not linked to a part are skipped.
    An empty `shortages` list means the build is fully covered.
    """
    project = _project(caller, project_id)
    build_qty = max(1, build_qty)
    rows = builds_service.shortage_analysis(
        caller.db,
        workspace_id=caller.ws.id,
        project=project,
        build_quantity=build_qty,
    )
    short = [row for row in rows if row.get("short_by", 0) > 0]
    return {
        "project_id": sid(project.id),
        "project_name": project.name,
        "build_qty": build_qty,
        "shortages": [
            compact(
                {
                    "part_id": row["part_id"],
                    "part_name": row["part_name"],
                    "part_url": part_url(row["part_id"]),
                    "required": row["required"],
                    "available": row["available"],
                    "short_by": row["short_by"],
                    "substitute_available": row.get("substitute_available"),
                }
            )
            for row in short
        ],
        "lines_checked": len(rows),
    }
