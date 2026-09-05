"""Printable pick lists (Track B4).

A pick list is the paper sheet an operator carries to the shelves: every
part a build needs, how many, in which unit, and **which storage location
to take each one from**, ordered so the walk happens once.

Read-only. Nothing here writes a ledger row, mutates a build, or touches
reservations — which is why no `audit_log` row is emitted (the universal
audit invariant covers *mutations*).

Three rules hold it together:

1. **`service.py::_required` stays the only quantity authority.** The
   sheet never re-derives demand from `project_entries.quantity`; the
   whole-build sheet reads `_required` directly and the per-stage sheet
   reads `stages.py::stage_allocations`, which is itself a slice of
   `_required`. Attrition therefore reaches the paper exactly as it
   reaches reservations and consumption — the operator picks the number
   the build will actually consume.

2. **Every quantity comes out of the stock service.** The per-location
   breakdown is `stock/service.py::bulk_stock_by_location`, a roll-up
   inside the one module allowed to aggregate `stock_entries`
   (CLAUDE.md's first hard invariant, ADR-0001). Quantities stay
   `Decimal` end to end — `stock_entries.quantity_delta` is
   `Numeric(18, 6)` since alembic 0074 and an `int()` anywhere on this
   path would truncate a measured quantity.

3. **Substitutes and meta-part members are reported, never picked from.**
   A shortfall is flagged, loudly, rather than quietly re-planned onto a
   substitute: substitute use is an explicit per-line decision at consume
   time (`shortage_analysis` calls substitute availability
   "informational"), and a pick sheet that sent the operator to fetch a
   part the consume screen was never told about would be worse than one
   that says "short 12". Each line still carries
   `alternates_available` — the same number `shortage_analysis` reports
   as `substitute_available` — so a `meta_part` line, whose stock lives
   entirely in its members, doesn't print as an unexplained shortfall
   against a build the build screen calls covered.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.domain._quantity import DEFAULT_UNIT, QUANTITY_ZERO, quantity_out
from app.domain.builds.models import Build, BuildStage
from app.domain.builds.service import (
    _candidate_part_ids,
    _consumable_entries,
    _required,
)
from app.domain.builds.stages import stage_allocations, stage_lines
from app.domain.lots.models import Lot
from app.domain.parts.models import Part
from app.domain.projects.models import Project, ProjectEntry
from app.domain.stock.service import bulk_stock_by_location
from app.domain.storage.models import StorageLocation

#: Label used on the sheet for stock that sits in no storage location.
#: The operator still has to account for it, so it becomes a real (last)
#: stop rather than being dropped.
UNASSIGNED_STOP_NAME = "Unassigned"


def _sort_key_for_stop(name: str | None) -> tuple[int, str]:
    """Walk order: named locations alphabetically, unassigned stock last.

    Storage names in this codebase are free-form strings that operators
    already use positionally ("A1", "A2", "Shelf 3 / Bin 4"), so a plain
    case-insensitive name sort is the closest thing to a physical order
    the schema knows about. `StorageLocation` has no coordinate or
    ordinal column; giving it one is a product decision, not something a
    read-only report should invent.
    """
    return (1, "") if name is None else (0, name.casefold())


def _allocate_picks(required: Decimal, buckets: list[dict]) -> list[dict]:
    """Take `required` units out of `buckets`, **draining them in place**.

    Greedy, largest bucket first: it opens the fewest bins, which is what
    an operator actually wants (fewer partial reels, fewer stops). Ties
    break on location name then lot id so two identical calls produce an
    identical sheet — a pick list that reshuffles between the screen and
    the printer is useless.

    The in-place drain is the load-bearing part. `project_entries` has no
    unique constraint on `(project_id, part_id)` — `POST /entries` has no
    dedupe guard and BOM import writes one row per CSV row — so the same
    part can legitimately sit on two BOM lines. Allocating each line
    against a fresh copy of the buckets would hand both lines the same
    reel: two lines of 10 against a bin holding 12 would each print
    "take 10, short 0", and the consume step would then refuse the build
    with "insufficient stock (have 12, want 20)". Sharing one mutable pool
    per part makes the second line short by 8, on paper, before anyone
    walks anywhere.

    Buckets are `{storage_location_id, storage_location_name, lot_id,
    lot_name, unit, available, unclaimed}`; `unclaimed` is decremented,
    `available` is what the bin actually holds and is what gets printed in
    the "At location" column. Returns one dict per contributing bucket with
    the `take` added; buckets contributing nothing are omitted.

    Every quantity here is an exact `Decimal` — `available` comes out of
    `bulk_stock_by_location` through `as_quantity`, and `required` is
    `_required`'s integer widened at the call site.
    """
    ordered = sorted(
        buckets,
        key=lambda b: (
            -b["unclaimed"],
            _sort_key_for_stop(b["storage_location_name"]),
            str(b["lot_id"] or ""),
        ),
    )
    remaining = required
    picks: list[dict] = []
    for bucket in ordered:
        if remaining <= QUANTITY_ZERO:
            break
        take = min(remaining, bucket["unclaimed"])
        if take <= QUANTITY_ZERO:
            continue
        bucket["unclaimed"] -= take
        picks.append({**bucket, "take": take})
        remaining -= take
    return picks


def _entry_rows(
    db: Session,
    *,
    workspace_id: UUID,
    project: Project,
    build: Build,
    stage: BuildStage | None,
) -> list[tuple[ProjectEntry, Part, Decimal, Decimal | None]]:
    """`(entry, part, required, portion_pct)` for every line the sheet covers.

    Whole-build: every consumable BOM entry at its full `_required`.
    Per-stage: only the entries the stage covers, at the stage's slice of
    `_required` (`stage_allocations`), with the stage's `portion_pct`
    carried through so the sheet can say "half of this line".
    """
    entries = {
        e.id: e
        for e in _consumable_entries(db, workspace_id=workspace_id, project=project)
    }
    parts = {
        entry_id: _part_in_workspace(db, workspace_id=workspace_id, entry=entry)
        for entry_id, entry in entries.items()
    }

    if stage is None:
        return [
            (
                entry,
                parts[entry_id],
                Decimal(_required(entry, parts[entry_id], build.quantity)),
                None,
            )
            for entry_id, entry in entries.items()
            if parts[entry_id] is not None
        ]

    allocation = stage_allocations(
        db, workspace_id=workspace_id, build=build, project=project
    ).get(stage.id, {})
    portions = {
        line.project_entry_id: Decimal(line.portion_pct)
        for line in stage_lines(db, workspace_id=workspace_id, stage_ids=[stage.id])
    }
    out: list[tuple[ProjectEntry, Part, Decimal, Decimal | None]] = []
    for entry_id, quantity in allocation.items():
        entry = entries.get(entry_id)
        part = parts.get(entry_id)
        if entry is None or part is None:
            continue
        if quantity <= 0:
            # Same filter `stages.py::consume_stage` applies: a portion
            # small enough to allocate zero units is nothing to fetch, and
            # a zero-quantity row on a paper sheet is just noise.
            continue
        out.append((entry, part, Decimal(quantity), portions.get(entry_id)))
    return out


def _part_in_workspace(
    db: Session, *, workspace_id: UUID, entry: ProjectEntry
) -> Part | None:
    """The BOM line's part, or None if it isn't this workspace's.

    The `workspace_id` re-check is the codebase-wide rule for a cross-table
    FK lookup (CLAUDE.md), not a suspicion about `project_entries`: a report
    is a bad place to start making exceptions to it.
    """
    if entry.part_id is None:
        return None
    part = db.get(Part, entry.part_id)
    return part if part is not None and part.workspace_id == workspace_id else None


def _name_maps(
    db: Session, *, workspace_id: UUID, buckets: list[dict]
) -> tuple[dict[UUID, str], dict[UUID, str | None]]:
    """Resolve storage / lot display names, workspace-filtered.

    Both lookups re-assert `workspace_id` even though the ids came out of
    ws-scoped ledger rows: cross-table FK lookups are followed by an
    equality check everywhere else in this codebase, and a report is not
    a good place to start making exceptions.
    """
    storage_ids = {b["storage_location_id"] for b in buckets if b["storage_location_id"]}
    lot_ids = {b["lot_id"] for b in buckets if b["lot_id"]}

    storage_names: dict[UUID, str] = {}
    if storage_ids:
        storage_names = {
            row.id: row.name
            for row in db.execute(
                select(StorageLocation)
                .where(StorageLocation.workspace_id == workspace_id)
                .where(StorageLocation.id.in_(storage_ids))
            ).scalars()
        }
    lot_names: dict[UUID, str | None] = {}
    if lot_ids:
        lot_names = {
            row.id: row.name
            for row in db.execute(
                select(Lot)
                .where(Lot.workspace_id == workspace_id)
                .where(Lot.id.in_(lot_ids))
            ).scalars()
        }
    return storage_names, lot_names


def _line_payload(
    entry: ProjectEntry,
    part: Part,
    *,
    required: Decimal,
    portion_pct: Decimal | None,
    on_hand: Decimal,
    alternates_available: Decimal,
    picks: list[dict],
) -> dict:
    planned = sum((p["take"] for p in picks), QUANTITY_ZERO)
    short_by = max(QUANTITY_ZERO, required - planned)
    return {
        "project_entry_id": str(entry.id),
        "part_id": str(part.id),
        "part_name": part.name,
        "mpn": part.mpn,
        "manufacturer": part.manufacturer,
        "internal_part_number": part.internal_part_number,
        # Reference designators from the BOM line — what the operator
        # matches against the board, and the only field that says *why*
        # this line needs 12 of something.
        "designators": list(entry.designators or []),
        # `unit_of_measure` is the part's canonical unit (alembic 0074).
        # It rides next to every quantity on the sheet so "120" is never
        # ambiguous between pieces and metres.
        "unit": part.unit_of_measure or DEFAULT_UNIT,
        "attrition_pct": float(entry.attrition_pct or 0),
        "portion_pct": float(portion_pct) if portion_pct is not None else None,
        "required": quantity_out(required),
        # The part's own total on hand. It can exceed `planned` without the
        # line being covered: when a part sits on two BOM lines they share
        # one pool, and the second line only gets what the first left.
        "on_hand": quantity_out(on_hand),
        # Stock in registered substitutes / meta-part members. Reported,
        # never picked from — same number `shortage_analysis` shows as
        # `substitute_available`, so the sheet and the build screen agree.
        "alternates_available": quantity_out(alternates_available),
        "planned": quantity_out(planned),
        "short_by": quantity_out(short_by),
        "is_short": short_by > QUANTITY_ZERO,
        # DISTINCT locations, not picks. Stock is bucketed per
        # `(storage, lot)`, so two lots on one shelf are two picks but one
        # stop on the walk — counting picks here would print "2 locations"
        # above a route with a single stop.
        "location_count": len({p["storage_location_id"] for p in picks}),
    }


def pick_list(
    db: Session,
    *,
    workspace_id: UUID,
    build: Build,
    project: Project,
    stage: BuildStage | None = None,
) -> dict:
    """The whole sheet: BOM-line demand plus the ordered shelf walk.

    Two views over one allocation, so the frontend never re-derives
    quantities:

    * ``lines`` — one row per BOM line in BOM order (`order_index`):
      identity, unit, `required`, `on_hand`, `alternates_available`,
      `planned`, `short_by`.
    * ``stops`` — one entry per storage location, **sorted by location
      name with unassigned stock last**, listing what to take there. This
      is the walk: an operator reads it top to bottom and visits each
      shelf once, even when one part is split across two bins and one bin
      serves three parts.

    Lines are walked in `order_index` order — the order they print in —
    because that is what decides who gets the stock when two lines want
    the same part (see `_allocate_picks`).
    """
    rows = _entry_rows(
        db, workspace_id=workspace_id, project=project, build=build, stage=stage
    )

    # Registered substitutes / meta-part members. Their stock is reported
    # per line as `alternates_available` but never picked from — see rule 3
    # in the module docstring. Reporting it keeps the sheet consistent with
    # what `shortage_analysis` shows on the build screen, which is the
    # number the operator planned against; without it a meta-part line
    # would print "short" against a build the app calls covered.
    alternates_by_part = {
        part.id: _candidate_part_ids(db, part=part) for _, part, _, _ in rows
    }
    by_location = bulk_stock_by_location(
        db,
        workspace_id=workspace_id,
        part_ids=[
            *(part.id for _, part, _, _ in rows),
            *(pid for ids in alternates_by_part.values() for pid in ids),
        ],
    )
    storage_names, lot_names = _name_maps(
        db,
        workspace_id=workspace_id,
        buckets=[b for buckets in by_location.values() for b in buckets],
    )

    # ONE mutable pool per part, shared by every BOM line that references
    # it. Two lines for the same part draw from the same shelves, so the
    # second sees what the first left. See `_allocate_picks`.
    pool_by_part = {
        part.id: [
            {
                "storage_location_id": b["storage_location_id"],
                "storage_location_name": (
                    storage_names.get(b["storage_location_id"])
                    if b["storage_location_id"]
                    else None
                ),
                "lot_id": b["lot_id"],
                "lot_name": lot_names.get(b["lot_id"]) if b["lot_id"] else None,
                "unit": b["unit"],
                "available": b["quantity"],
                "unclaimed": b["quantity"],
            }
            for b in by_location.get(part.id, [])
        ]
        for _, part, _, _ in rows
    }

    lines: list[dict] = []
    stops_by_location: dict[UUID | None, dict] = {}

    # BOM order decides who gets the stock when two lines want the same
    # part: the sheet reads top to bottom, so the first line on it is the
    # first served.
    for entry, part, required, portion_pct in sorted(
        rows, key=lambda r: r[0].order_index
    ):
        pool = pool_by_part[part.id]
        on_hand = sum((b["available"] for b in pool), QUANTITY_ZERO)
        alternates_available = sum(
            (
                bucket["quantity"]
                for alternate_id in alternates_by_part[part.id]
                for bucket in by_location.get(alternate_id, [])
            ),
            QUANTITY_ZERO,
        )
        picks = _allocate_picks(required, pool)
        lines.append(
            _line_payload(
                entry,
                part,
                required=required,
                portion_pct=portion_pct,
                on_hand=on_hand,
                alternates_available=alternates_available,
                picks=picks,
            )
        )
        for pick in picks:
            stop = stops_by_location.setdefault(
                pick["storage_location_id"],
                {
                    "storage_location_id": (
                        str(pick["storage_location_id"])
                        if pick["storage_location_id"]
                        else None
                    ),
                    "storage_location_name": pick["storage_location_name"]
                    or UNASSIGNED_STOP_NAME,
                    "_sort": _sort_key_for_stop(pick["storage_location_name"]),
                    "picks": [],
                },
            )
            stop["picks"].append(
                {
                    "project_entry_id": str(entry.id),
                    "part_id": str(part.id),
                    "part_name": part.name,
                    "mpn": part.mpn,
                    "designators": list(entry.designators or []),
                    "lot_id": str(pick["lot_id"]) if pick["lot_id"] else None,
                    "lot_name": pick["lot_name"],
                    "quantity": quantity_out(pick["take"]),
                    # The LEDGER's unit stamp, not the part's. A pick is a
                    # fact about rows already written; `_quantity.py` exists
                    # because a part-level-only unit lets an edit
                    # retroactively reinterpret history. The line's
                    # `required` is a plan, so that one uses the part's unit.
                    # Identical today — `DEFAULT_UNIT` is all anything writes.
                    "unit": pick["unit"],
                    "available": quantity_out(pick["available"]),
                }
            )

    stops = sorted(stops_by_location.values(), key=lambda s: s["_sort"])
    for stop in stops:
        stop.pop("_sort")
        stop["picks"].sort(key=lambda p: (p["part_name"] or "").casefold())

    short_lines = [line for line in lines if line["is_short"]]
    return {
        "build": {
            "id": str(build.id),
            "name": build.name,
            "quantity": build.quantity,
            "status": build.status,
        },
        "project": {"id": str(project.id), "name": project.name},
        "stage": (
            None
            if stage is None
            else {
                "id": str(stage.id),
                "name": stage.name,
                "sequence": stage.sequence,
                "status": stage.status,
            }
        ),
        "generated_at": utcnow().isoformat(),
        "lines": lines,
        "stops": stops,
        "totals": {
            "lines": len(lines),
            "short_lines": len(short_lines),
            "stops": len(stops),
        },
    }
