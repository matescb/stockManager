"""Kitting (Track B3).

Consolidate everything a build needs into one staging location, so the
operator carries a tray to the bench instead of walking the shelves.
Mirrors PartsBox's kitting.

Four rules hold this together:

1. **A kit is a move, not a mutation.** Every unit relocated is written as
   a matched ``move_out`` / ``move_in`` pair through
   ``stock/service.py::move_quantity``. Total on-hand for the part is
   unchanged by a kit; only its distribution across locations changes.
   Nothing here ever constructs a ledger row itself.

2. **`_required` stays the only quantity authority.** A kit never
   re-derives demand from ``project_entries.quantity`` — the whole-build
   pass takes ``service.py::_required`` and the per-stage pass takes
   ``stages.py::stage_allocations`` (which is itself a slice of
   ``_required``). Both attrition sources therefore apply exactly once,
   and the tray is stocked with precisely what the shortage table the
   operator planned against said it would need.

3. **A kit tops the staging location up; it does not add to it.** The
   quantity moved is ``required - already_at_staging``, so re-running a kit
   is a no-op rather than a second trayful. That is what makes the
   operation idempotent-safe under a retried request, a double-clicked
   button, or a partial kit the operator wants to complete after a
   delivery lands.

4. **Reservations are untouched.** Reserve rows carry no
   ``storage_location_id`` and a kit writes only ``status='on_hand'``
   rows, so ``stock/service.py::reserved_quantity`` is invariant across a
   kit. Kitting is a physical relocation, not an allocation: it neither
   consumes a reservation (that is consume's job, per stage) nor strands
   one (a reservation is not bound to a location, so material can be moved
   without orphaning it). See ``docs/domain/builds-and-bom.md``.

Partial availability **moves what exists and reports the shortfall**
rather than refusing: an operator who is 10 short of 100 resistors still
wants the 90 on the tray. The refusing alternative hands back nothing and
forces the shelf-walk the feature exists to remove.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domain._quantity import quantity_out
from app.domain.builds.models import Build, BuildStage
from app.domain.builds.service import BuildError, _consumable_entries, _required
from app.domain.builds.stages import stage_allocations
from app.domain.parts.models import Part
from app.domain.projects.models import Project, ProjectEntry
from app.domain.stock.service import (
    current_quantity,
    lock_parts_for_stock_write,
    move_quantity,
    stock_summary_for_part,
)
from app.domain.storage.models import StorageLocation

log = get_logger(__name__)

_KITTABLE_BUILD_STATUSES = ("planned", "in_progress")


@dataclass(frozen=True)
class KitSource:
    """One bucket the kit draws from — a (storage, lot) pair holding stock."""

    storage_location_id: UUID | None
    lot_id: UUID | None
    quantity: Decimal


@dataclass
class KitLine:
    """What the kit will do (or did) for one part.

    Keyed by **part**, not by BOM entry: two BOM lines calling for the same
    part are one pile on the tray, and planning them separately would let
    each of them claim the same reel.
    """

    part_id: UUID
    part_name: str
    project_entry_ids: list[UUID]
    required: Decimal
    at_staging: Decimal
    to_move: Decimal
    sources: list[KitSource] = field(default_factory=list)

    @property
    def moving(self) -> Decimal:
        return sum((s.quantity for s in self.sources), Decimal(0))

    @property
    def short_by(self) -> Decimal:
        return max(Decimal(0), self.to_move - self.moving)


def resolve_staging(
    db: Session, *, workspace_id: UUID, storage_location_id: UUID
) -> StorageLocation:
    """The staging location for a kit, validated against the workspace.

    Deliberately a **request parameter**, not a column on `builds`: which
    tray/cart/shelf is free is a property of today's shop floor, not of the
    build, and the same build may be kitted twice onto different trays (a
    re-kit after a delivery, a second bench). A per-build column would need
    a default, an edit surface, and a request-level override anyway — the
    override is the whole feature, so it is the only thing that exists.
    """
    staging = db.get(StorageLocation, storage_location_id)
    if staging is None or staging.workspace_id != workspace_id:
        raise BuildError("staging location not found")
    if staging.archived_at is not None:
        raise BuildError("staging location is archived")
    if staging.is_full:
        raise BuildError("staging location is marked full")
    return staging


def required_by_part(
    db: Session,
    *,
    workspace_id: UUID,
    build: Build,
    project: Project,
    stage: BuildStage | None,
) -> tuple[dict[UUID, Decimal], dict[UUID, list[UUID]]]:
    """`({part_id: required}, {part_id: [project_entry_id, …]})` for a kit.

    `stage=None` is the whole build — every consumable BOM entry at its
    `_required` quantity, the same dict `service.py::consume` builds. A
    stage takes its own allocation instead, which `stage_allocations`
    derives from that same `_required` by portion.
    """
    # BOM order, so the kit list reads in the same order as the shortage
    # table the operator planned against.
    entries: list[ProjectEntry] = _consumable_entries(
        db, workspace_id=workspace_id, project=project
    )
    if stage is None:
        wanted = {
            entry.id: Decimal(_required(entry, part, build.quantity))
            for entry, part in ((e, db.get(Part, e.part_id)) for e in entries)
            if part is not None
        }
    else:
        wanted = {
            entry_id: Decimal(quantity)
            for entry_id, quantity in stage_allocations(
                db, workspace_id=workspace_id, build=build, project=project
            )
            .get(stage.id, {})
            .items()
        }

    per_part: dict[UUID, Decimal] = {}
    entry_ids: dict[UUID, list[UUID]] = {}
    for entry in entries:
        quantity = wanted.get(entry.id, Decimal(0))
        if entry.part_id is None or quantity <= 0:
            continue
        per_part[entry.part_id] = per_part.get(entry.part_id, Decimal(0)) + quantity
        entry_ids.setdefault(entry.part_id, []).append(entry.id)
    return per_part, entry_ids


def _sources_for(
    db: Session,
    *,
    workspace_id: UUID,
    part_id: UUID,
    staging_id: UUID,
    wanted: Decimal,
) -> list[KitSource]:
    """Pick the buckets the kit draws `wanted` units from.

    Buckets come from the ledger's own roll-up (`stock_summary_for_part`)
    so the "current stock is SUM(quantity_delta)" invariant has one
    expression. The staging location itself is excluded — it is the
    destination, and its contents were already netted off `wanted`.

    Order is **largest bucket first**, tie-broken on the ids so two runs of
    the same plan pick the same buckets. Largest-first minimises both the
    number of bins the operator has to visit and the number of ledger rows
    the kit writes; taking the tail bucket partially is unavoidable either
    way.
    """
    buckets = [
        (
            row["storage_location_id"],
            row["lot_id"],
            Decimal(row["quantity"]),
        )
        for row in stock_summary_for_part(db, workspace_id=workspace_id, part_id=part_id)
        if row["storage_location_id"] != staging_id and Decimal(row["quantity"]) > 0
    ]
    buckets.sort(key=lambda b: (-b[2], str(b[0] or ""), str(b[1] or "")))

    out: list[KitSource] = []
    remaining = wanted
    for storage_location_id, lot_id, available in buckets:
        if remaining <= 0:
            break
        take = min(remaining, available)
        out.append(
            KitSource(
                storage_location_id=storage_location_id,
                lot_id=lot_id,
                quantity=take,
            )
        )
        remaining -= take
    return out


def plan_kit(
    db: Session,
    *,
    workspace_id: UUID,
    build: Build,
    project: Project,
    stage: BuildStage | None,
    staging: StorageLocation,
) -> list[KitLine]:
    """What a kit onto `staging` would move, and what it would fall short of.

    Read-only. `execute_kit` calls this under the per-part locks it already
    holds, so the plan it acts on cannot go stale inside its transaction;
    called on its own (the preview route) it is a best-effort snapshot,
    exactly like the shortage table beside it.
    """
    per_part, entry_ids = required_by_part(
        db, workspace_id=workspace_id, build=build, project=project, stage=stage
    )
    lines: list[KitLine] = []
    # `per_part` is built in BOM order and dicts preserve insertion order.
    for part_id in per_part:
        part = db.get(Part, part_id)
        if part is None or part.workspace_id != workspace_id:
            continue
        required = per_part[part_id]
        # `bucket_match=False` with a storage filter: everything of this
        # part already on the tray, across every lot sitting there.
        at_staging = Decimal(
            current_quantity(
                db,
                workspace_id=workspace_id,
                part_id=part_id,
                storage_location_id=staging.id,
            )
        )
        to_move = max(Decimal(0), required - at_staging)
        lines.append(
            KitLine(
                part_id=part_id,
                part_name=part.name,
                project_entry_ids=entry_ids[part_id],
                required=required,
                at_staging=at_staging,
                to_move=to_move,
                sources=(
                    _sources_for(
                        db,
                        workspace_id=workspace_id,
                        part_id=part_id,
                        staging_id=staging.id,
                        wanted=to_move,
                    )
                    if to_move > 0
                    else []
                ),
            )
        )
    return lines


def execute_kit(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    build: Build,
    project: Project,
    stage: BuildStage | None,
    staging: StorageLocation,
) -> list[KitLine]:
    """Move the plan onto the tray. All-or-nothing within the request.

    Locks every part the kit touches up front, in the deterministic
    UUID-string order `lock_parts_for_stock_write` imposes, *before* the
    plan is read — so the availability the plan saw is the availability
    `move_quantity` re-checks, and a concurrent consume of the same parts
    queues behind us rather than emptying a bucket mid-kit.

    A shortfall is not an error: the lines that can be filled are moved and
    the shortfall is reported per part. A genuine failure (destination
    constraint violation, archived location, a bucket that vanished) raises
    and rolls the whole kit back — a half-written tray is worse than none.
    """
    if build.archived_at is not None:
        raise BuildError("build is archived")
    if build.status not in _KITTABLE_BUILD_STATUSES:
        raise BuildError(f"build is {build.status}")
    if stage is not None and stage.status == "complete":
        raise BuildError(f"stage '{stage.name}' is already complete")

    per_part, _ = required_by_part(
        db, workspace_id=workspace_id, build=build, project=project, stage=stage
    )
    lock_parts_for_stock_write(db, workspace_id=workspace_id, part_ids=list(per_part))

    lines = plan_kit(
        db,
        workspace_id=workspace_id,
        build=build,
        project=project,
        stage=stage,
        staging=staging,
    )
    moved_rows = 0
    for line in lines:
        for source in line.sources:
            move_quantity(
                db,
                workspace_id=workspace_id,
                user_id=user_id,
                part_id=line.part_id,
                quantity=source.quantity,
                source_storage_location_id=source.storage_location_id,
                source_lot_id=source.lot_id,
                destination_storage_location_id=staging.id,
                comments=f"kit for build {build.name}",
                build_id=build.id,
                build_stage_id=stage.id if stage else None,
            )
            moved_rows += 1

    log.info(
        "build kitted",
        extra={
            "workspace_id": str(workspace_id),
            "build_id": str(build.id),
            "build_stage_id": str(stage.id) if stage else None,
            "storage_location_id": str(staging.id),
            "moved_rows": moved_rows,
            "short_lines": sum(1 for line in lines if line.short_by > 0),
        },
    )
    return lines


def serialize_kit(
    db: Session,
    *,
    workspace_id: UUID,
    build: Build,
    stage: BuildStage | None,
    staging: StorageLocation,
    lines: list[KitLine],
    executed: bool,
) -> dict:
    """Response body for both the preview and the executed kit.

    One shape for both so the UI renders the "what will move" table and the
    "what moved" result with the same component. Quantities go through
    `quantity_out`, which emits a whole number as an `int` and refuses to
    truncate a fractional one.
    """
    source_ids = {
        source.storage_location_id
        for line in lines
        for source in line.sources
        if source.storage_location_id is not None
    }
    names: dict[UUID, str] = {}
    if source_ids:
        names = {
            row.id: row.name
            for row in db.execute(
                select(StorageLocation)
                .where(StorageLocation.workspace_id == workspace_id)
                .where(StorageLocation.id.in_(source_ids))
            ).scalars()
        }

    return {
        "build_id": str(build.id),
        "build_stage_id": str(stage.id) if stage else None,
        "storage_location_id": str(staging.id),
        "storage_location_name": staging.name,
        "executed": executed,
        "lines": [
            {
                "part_id": str(line.part_id),
                "part_name": line.part_name,
                "project_entry_ids": [str(e) for e in line.project_entry_ids],
                "required": quantity_out(line.required),
                "at_staging": quantity_out(line.at_staging),
                "to_move": quantity_out(line.to_move),
                "moving": quantity_out(line.moving),
                "short_by": quantity_out(line.short_by),
                "sources": [
                    {
                        "storage_location_id": (
                            str(source.storage_location_id)
                            if source.storage_location_id
                            else None
                        ),
                        "storage_location_name": (
                            names.get(source.storage_location_id)
                            if source.storage_location_id
                            else None
                        ),
                        "lot_id": str(source.lot_id) if source.lot_id else None,
                        "quantity": quantity_out(source.quantity),
                    }
                    for source in line.sources
                ],
            }
            for line in lines
        ],
        "totals": {
            "lines": len(lines),
            "moving": quantity_out(sum((line.moving for line in lines), Decimal(0))),
            "short_by": quantity_out(sum((line.short_by for line in lines), Decimal(0))),
            "short_lines": sum(1 for line in lines if line.short_by > 0),
        },
    }
