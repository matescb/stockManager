"""Build / consume-from-BOM service.

A build runs against a project's BOM. Consuming the build emits
'build_consume' ledger rows for each input line and, if the project has
an associated sub-assembly part, a 'build_produce' row + a Lot tagged
source_type='build', source_build_id=build.id."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from math import ceil
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domain.builds.models import Build
from app.domain.builds.schemas import ConsumeIn

log = get_logger(__name__)
from app.domain.lots.models import Lot
from app.domain.parts.models import Part, PartMetaMember, PartSubstitute
from app.domain.projects.models import Project, ProjectEntry
from app.domain.stock.models import StockEntry
from app.domain.stock.service import current_quantity, lock_parts_for_stock_write
from app.domain.storage.models import StorageLocation


class BuildError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _required(entry: ProjectEntry, part: Part | None, build_qty: int) -> int:
    """Required quantity for one entry across `build_qty` builds, including
    attrition. Spec §19.3: ``ceil(qty * builds * (1 + attrition%))`` but at
    least ``qty * builds + attrition_min_quantity``."""
    base = float(entry.quantity) * build_qty
    pct = float(part.attrition_percentage) if part else 0.0
    floor_extra = part.attrition_min_quantity if part else 0
    target = base * (1 + pct / 100.0)
    target = max(target, base + floor_extra)
    return int(ceil(target))


def _candidate_part_ids(db: Session, *, part: Part) -> list[UUID]:
    """Parts that may be used in place of `part` for build consumption.
    For a meta-part: its registered members. For a regular part:
    registered substitutes (one-way main→sub or bidirectional)."""
    if part.part_type == "meta":
        rows = list(
            db.execute(
                select(PartMetaMember.part_id).where(PartMetaMember.meta_part_id == part.id)
            ).scalars()
        )
        return [r for r in rows if r != part.id]

    sub_rows = list(
        db.execute(
            select(PartSubstitute).where(
                (PartSubstitute.part_id == part.id)
                | (
                    (PartSubstitute.substitute_part_id == part.id)
                    & (PartSubstitute.direction == "bidirectional")
                )
            )
        ).scalars()
    )
    out: list[UUID] = []
    for s in sub_rows:
        sub_id = s.substitute_part_id if s.part_id == part.id else s.part_id
        if sub_id != part.id:
            out.append(sub_id)
    return out


def shortage_analysis(
    db: Session, *, workspace_id: UUID, project: Project, build_quantity: int
) -> list[dict]:
    """Per-entry analysis of needed vs. available stock. Considers main part
    + any registered bidirectional or one_way substitutes (for regular
    parts) or meta-part members (for meta parts)."""
    entries = list(
        db.execute(
            select(ProjectEntry)
            .where(ProjectEntry.workspace_id == workspace_id)
            .where(ProjectEntry.project_id == project.id)
            .order_by(ProjectEntry.order_index)
        ).scalars()
    )
    out: list[dict] = []
    for e in entries:
        if e.dnp:
            continue
        if e.entry_type not in ("part", "meta_part"):
            continue
        if e.part_id is None:
            continue  # unmatched / non_part
        part = db.get(Part, e.part_id)
        if part is None:
            continue
        required = _required(e, part, build_quantity)
        # For meta-parts there's typically no on-hand of the meta itself —
        # all stock lives in the member parts. Still report the meta's
        # own on-hand for completeness.
        available = current_quantity(db, workspace_id=workspace_id, part_id=part.id)

        sub_ids = _candidate_part_ids(db, part=part)
        sub_avail = sum(
            current_quantity(db, workspace_id=workspace_id, part_id=sid) for sid in sub_ids
        )

        out.append(
            {
                "project_entry_id": str(e.id),
                "part_id": str(part.id),
                "part_name": part.name,
                "required": required,
                "available": available,
                "substitute_ids": [str(s) for s in sub_ids],
                "substitute_available": sub_avail,
                "short_by": max(0, required - (available + sub_avail)),
            }
        )
    return out


def _consumable_entries(
    db: Session, *, workspace_id: UUID, project: Project
) -> list[ProjectEntry]:
    """BOM entries that should reserve stock — same filter as shortage_analysis."""
    entries = list(
        db.execute(
            select(ProjectEntry)
            .where(ProjectEntry.workspace_id == workspace_id)
            .where(ProjectEntry.project_id == project.id)
            .order_by(ProjectEntry.order_index)
        ).scalars()
    )
    return [
        e
        for e in entries
        if not e.dnp and e.entry_type in ("part", "meta_part") and e.part_id is not None
    ]


def apply_reservations(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    build: Build,
    project: Project,
) -> int:
    """Write one `status='reserved'` row per consumable BOM entry.

    Returns the number of reservation rows written. A reservation is keyed
    only by (build_id, part_id, project_id, entry quantity) — no storage
    location or lot is bound, since the consumer picks those at consume-time.
    """
    # BE2-008: lock every distinct part_id we're about to touch, in
    # deterministic order, before any read. Without this, concurrent
    # apply_reservations / release_reservations / consume on overlapping
    # BOMs race on the reserved ledger and can either double-write or
    # under-write the counter rows.
    consumable = _consumable_entries(db, workspace_id=workspace_id, project=project)
    lock_parts_for_stock_write(
        db,
        workspace_id=workspace_id,
        part_ids=[e.part_id for e in consumable if e.part_id is not None],
    )
    now = _now()
    written = 0
    for e in consumable:
        part = db.get(Part, e.part_id)
        if part is None:
            continue
        qty = _required(e, part, build.quantity)
        if qty <= 0:
            continue
        row = StockEntry(
            workspace_id=workspace_id,
            part_id=part.id,
            quantity_delta=qty,
            status="reserved",
            operation_type="reserve",
            build_id=build.id,
            project_id=project.id,
            occurred_at=now,
            created_by=user_id,
        )
        db.add(row)
        written += 1
    if written:
        db.flush()
    return written


def release_reservations(
    db: Session, *, workspace_id: UUID, user_id: UUID | None, build: Build
) -> int:
    """Write a counter-row for every outstanding reserve row tied to `build`.

    "Outstanding" = a reserve row whose net contribution is still positive,
    i.e. a row with `operation_type='reserve'` for which no matching
    `operation_type='release'` (`related_entry_id == reserve.id`) yet exists.
    Idempotent: returns 0 if there is nothing to release.
    """
    reserve_rows = list(
        db.execute(
            select(StockEntry)
            .where(StockEntry.workspace_id == workspace_id)
            .where(StockEntry.build_id == build.id)
            .where(StockEntry.status == "reserved")
            .where(StockEntry.operation_type == "reserve")
        ).scalars()
    )
    if not reserve_rows:
        return 0
    # BE2-008: aggregate distinct part_ids from the reserve set, sort,
    # take the per-part lock before reading the release-counter set.
    # This serialises archive-while-consuming and any release flow that
    # races with another build releasing the same parts.
    lock_parts_for_stock_write(
        db,
        workspace_id=workspace_id,
        part_ids=[r.part_id for r in reserve_rows],
    )
    released_ids = set(
        db.execute(
            select(StockEntry.related_entry_id)
            .where(StockEntry.workspace_id == workspace_id)
            .where(StockEntry.build_id == build.id)
            .where(StockEntry.status == "reserved")
            .where(StockEntry.operation_type == "release")
            .where(StockEntry.related_entry_id.is_not(None))
        ).scalars()
    )
    now = _now()
    written = 0
    for r in reserve_rows:
        if r.id in released_ids:
            continue
        counter = StockEntry(
            workspace_id=workspace_id,
            part_id=r.part_id,
            quantity_delta=-r.quantity_delta,
            status="reserved",
            operation_type="release",
            related_entry_id=r.id,
            build_id=build.id,
            project_id=r.project_id,
            occurred_at=now,
            created_by=user_id,
        )
        db.add(counter)
        written += 1
    if written:
        db.flush()
    return written


def consume(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    build: Build,
    project: Project,
    payload: ConsumeIn,
) -> dict:
    """Apply a build's consumption plan. All-or-nothing within the request."""
    if build.status not in ("planned", "in_progress"):
        raise BuildError(f"build is {build.status}")

    # Take every lock this transaction needs up front, in deterministic
    # UUID-string order, before any read or write. Bundles together the
    # part_ids from: the project's BOM (touched by `release_reservations`),
    # the consume lines themselves, and the optional sub-assembly output.
    # Calling `lock_parts_for_stock_write` here means the inner
    # `release_reservations` / per-line writes / `output_lot` insert all
    # acquire their lock as a re-entrant no-op (Postgres advisory locks
    # are transaction-scoped). Without bundling, two concurrent consumes
    # touching overlapping BOM ∪ line sets could acquire locks in
    # different orders → AB/BA deadlock.
    bom_part_ids = [
        e.part_id
        for e in _consumable_entries(db, workspace_id=workspace_id, project=project)
        if e.part_id is not None
    ]
    line_part_ids = [line.part_id for line in payload.lines]
    output_part_ids: list[UUID] = (
        [project.associated_subassembly_part_id]
        if project.associated_subassembly_part_id is not None
        else []
    )
    lock_parts_for_stock_write(
        db,
        workspace_id=workspace_id,
        part_ids=[*bom_part_ids, *line_part_ids, *output_part_ids],
    )

    # Release any outstanding reservations first so the consumption itself
    # doesn't get double-counted against on_hand+reserved.
    release_reservations(db, workspace_id=workspace_id, user_id=user_id, build=build)

    entries_by_id: dict[UUID, ProjectEntry] = {
        e.id: e
        for e in db.query(ProjectEntry)
        .filter(ProjectEntry.workspace_id == workspace_id, ProjectEntry.project_id == project.id)
        .all()
    }

    # Pre-pass: aggregate demand per exact (part_id, lot_id, storage_location_id)
    # tuple before any per-line write. Without this, two BOM entries can each
    # claim 60 of the same 100-piece reel and each pass `current_quantity >=
    # line.quantity` independently — both lines write -60 and the lot ends up
    # at -20 (BE CRIT-3). Aggregating first means every tuple's total demand
    # is checked once, against the same per-tuple availability the per-line
    # path would have used.
    demand_by_tuple: dict[tuple[UUID, UUID | None, UUID | None], int] = {}
    for line in payload.lines:
        key = (line.part_id, line.lot_id, line.storage_location_id)
        demand_by_tuple[key] = demand_by_tuple.get(key, 0) + line.quantity
    for (part_id, lot_id, storage_location_id), total_demand in demand_by_tuple.items():
        avail = current_quantity(
            db,
            workspace_id=workspace_id,
            part_id=part_id,
            lot_id=lot_id,
            storage_location_id=storage_location_id,
            bucket_match=True,
        )
        if total_demand > avail:
            raise BuildError(
                f"insufficient stock for part {part_id} (have {avail}, want {total_demand})"
            )

    # Sum requested quantity per (entry, part) so we can validate against required
    requested_by_entry: dict[UUID, int] = {}
    consumed_entries: list[StockEntry] = []
    now = _now()

    for line in payload.lines:
        e = entries_by_id.get(line.project_entry_id)
        if e is None:
            raise BuildError(f"project entry {line.project_entry_id} not in this project")
        if e.entry_type not in ("part", "meta_part") or e.part_id is None:
            raise BuildError(f"project entry {e.id} has no part to consume")
        if e.dnp:
            raise BuildError(f"project entry {e.id} is DNP")

        # Validate the chosen part is the entry's main part, a registered
        # substitute, or (for meta-part entries) a meta member.
        if line.part_id != e.part_id:
            entry_part = db.get(Part, e.part_id)
            if entry_part is None:
                raise BuildError(f"entry {e.id} has missing part")
            if line.part_id not in _candidate_part_ids(db, part=entry_part):
                kind = (
                    "a meta-part member"
                    if entry_part.part_type == "meta"
                    else "a substitute"
                )
                raise BuildError(f"part {line.part_id} is not {kind} for entry {e.id}")

        # Validate caller-supplied lot / storage against the workspace
        # BEFORE the availability check. current_quantity is ws-filtered so
        # a foreign lot/storage is masked as "0 available" — that's defense
        # in depth, not a contract. Validating here keeps the failure mode
        # explicit and prevents a future refactor of current_quantity from
        # silently re-opening a write-side cross-workspace FK leak.
        if line.lot_id is not None:
            lot = db.get(Lot, line.lot_id)
            if lot is None or lot.workspace_id != workspace_id:
                raise BuildError(f"lot {line.lot_id} not in workspace")
        if line.storage_location_id is not None:
            sl = db.get(StorageLocation, line.storage_location_id)
            if sl is None or sl.workspace_id != workspace_id:
                raise BuildError(f"storage {line.storage_location_id} not in workspace")

        # Verify stock available
        avail = current_quantity(
            db,
            workspace_id=workspace_id,
            part_id=line.part_id,
            storage_location_id=line.storage_location_id,
            lot_id=line.lot_id,
            bucket_match=True,
        )
        if line.quantity > avail:
            raise BuildError(
                f"insufficient stock for part {line.part_id} (have {avail}, want {line.quantity})"
            )

        entry_row = StockEntry(
            workspace_id=workspace_id,
            part_id=line.part_id,
            lot_id=line.lot_id,
            storage_location_id=line.storage_location_id,
            quantity_delta=-line.quantity,
            status="on_hand",
            operation_type="build_consume",
            build_id=build.id,
            project_id=project.id,
            occurred_at=now,
            created_by=user_id,
        )
        db.add(entry_row)
        db.flush()
        consumed_entries.append(entry_row)
        requested_by_entry[e.id] = requested_by_entry.get(e.id, 0) + line.quantity

    # Required-coverage check: every non-DNP/part entry in the project must be
    # covered to at least its required quantity.
    for e in entries_by_id.values():
        if e.dnp or e.entry_type not in ("part", "meta_part") or e.part_id is None:
            continue
        part = db.get(Part, e.part_id)
        if part is None:
            continue
        req = _required(e, part, build.quantity)
        got = requested_by_entry.get(e.id, 0)
        if got < req:
            raise BuildError(
                f"entry {e.id} ({part.name}) under-consumed (need {req}, supplied {got})"
            )

    # Optional output: produce sub-assembly lot if the project has one
    output_lot: Lot | None = None
    output_entry: StockEntry | None = None
    if project.associated_subassembly_part_id is not None:
        sub_part = db.get(Part, project.associated_subassembly_part_id)
        if sub_part is None or sub_part.workspace_id != workspace_id:
            raise BuildError("project's sub-assembly part not in workspace")

        storage = None
        if payload.output_storage_location_id is not None:
            storage = db.get(StorageLocation, payload.output_storage_location_id)
            if storage is None or storage.workspace_id != workspace_id:
                raise BuildError("output storage not in workspace")
            if storage.archived_at is not None or storage.is_full:
                raise BuildError("output storage archived or full")

        output_lot = Lot(
            workspace_id=workspace_id,
            part_id=sub_part.id,
            name=payload.output_lot_name or f"{build.name}-out",
            source_type="build",
            source_build_id=build.id,
            purchase_quantity=build.quantity,
            created_by=user_id,
            updated_by=user_id,
        )
        db.add(output_lot)
        db.flush()

        output_entry = StockEntry(
            workspace_id=workspace_id,
            part_id=sub_part.id,
            lot_id=output_lot.id,
            storage_location_id=storage.id if storage else None,
            quantity_delta=build.quantity,
            status="on_hand",
            operation_type="build_produce",
            build_id=build.id,
            project_id=project.id,
            occurred_at=now,
            created_by=user_id,
        )
        db.add(output_entry)
        db.flush()

    build.status = "complete"
    build.started_at = build.started_at or now
    build.completed_at = now
    build.output_lot_id = output_lot.id if output_lot else None
    build.updated_by = user_id

    log.info(
        "build consumed",
        extra={
            "workspace_id": str(workspace_id),
            "build_id": str(build.id),
            "project_id": str(project.id),
            "consumed_lines": len(consumed_entries),
            "output_lot_id": str(output_lot.id) if output_lot else None,
        },
    )
    return {
        "build_id": str(build.id),
        "status": build.status,
        "consumed_entries": [str(s.id) for s in consumed_entries],
        "output_lot_id": str(output_lot.id) if output_lot else None,
        "output_stock_entry_id": str(output_entry.id) if output_entry else None,
    }
