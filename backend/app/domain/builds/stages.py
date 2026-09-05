"""Multi-stage builds (Track B2).

A build may be assembled across several stages instead of one all-at-once
consume. Each stage names a subset of the project BOM and the portion of
each line it takes, so a partially-built device is tracked accurately and
stock is drawn down progressively.

Three rules hold this together:

1. **`_required` stays the only quantity authority.** A stage never
   re-derives demand from `project_entries.quantity`; it takes the
   attrition-adjusted whole-build integer that
   `service.py::_required` returns and slices it by portion. Both attrition
   sources therefore apply exactly once, and a set of stages whose portions
   sum to 100% consumes exactly what a single-pass build would.

2. **Reservations are taken once, up front, for the whole build.** Stage
   creation writes no reserve rows. Each stage consume releases only the
   slice it is about to consume via
   `service.py::release_reservation_amounts`, so nothing is double-counted
   against `stock/service.py::reserved_quantity` and later stages stay
   reserved. See `docs/domain/builds-and-bom.md`.

3. **A build with no stages is untouched.** Every function here is only
   reachable through the `/stages` routes; `service.py::consume` is the
   single-pass path and behaves exactly as it did before this module
   existed.
"""
from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.time import utcnow
from app.domain.builds.models import Build, BuildStage, BuildStageLine
from app.domain.builds.schemas import BuildStageCreateIn, StageConsumeIn
from app.domain.builds.service import (
    BuildError,
    _candidate_part_ids,
    _required,
    apply_consume_lines,
    complete_build,
    lock_for_consume,
    produce_output,
    project_entries_by_id,
    release_reservation_amounts,
    release_reservations,
)
from app.domain.parts.models import Part
from app.domain.projects.models import Project, ProjectEntry
from app.domain.stock.service import current_quantity

log = get_logger(__name__)

_ACTIVE_STAGE_STATUSES = ("planned", "in_progress")


def _allocate(total: int | Decimal, portions: list[tuple[UUID, Decimal]]) -> dict[UUID, int]:
    """Split `total` across stages by portion percentage.

    Allocation is **cumulative**, not per-stage independent: stage *n* gets
    `ceil(total * Σportions[0..n] / 100) - ceil(total * Σportions[0..n-1] / 100)`.

    Rounding each stage independently would either lose or invent units —
    `ceil(103 * 50%) * 2 == 104 != 103`. Cumulative ceiling makes the parts
    sum to exactly `total` whenever the portions sum to 100, which is the
    property that keeps a staged build consuming exactly what the equivalent
    single-pass build consumes. Stock is integer-only, so every boundary is
    rounded up (same direction, and for the same reason, as `_required`).

    `total` is whatever `_required` returns — an `int` today. It is accepted
    as `int | Decimal` and the cap is ceiled rather than compared raw, so a
    later dimension-aware `_required` (see its docstring: measured parts
    will quantize to their unit instead of ceiling) cannot make `min()`
    hand back a `Decimal` and leak a fractional stage quantity into rows
    the rest of this module treats as integers.
    """
    cap = int(Decimal(total).to_integral_value(rounding=ROUND_CEILING))
    out: dict[UUID, int] = {}
    cumulative = Decimal(0)
    previous = 0
    for stage_id, pct in portions:
        cumulative += pct
        upto = int(
            (Decimal(total) * cumulative / 100).to_integral_value(rounding=ROUND_CEILING)
        )
        upto = min(upto, cap)
        out[stage_id] = max(0, upto - previous)
        previous = upto
    return out


def list_stages(db: Session, *, workspace_id: UUID, build: Build) -> list[BuildStage]:
    """Active stages of a build, in consumption order."""
    return list(
        db.execute(
            select(BuildStage)
            .where(BuildStage.workspace_id == workspace_id)
            .where(BuildStage.build_id == build.id)
            .where(BuildStage.archived_at.is_(None))
            .order_by(BuildStage.sequence, BuildStage.created_at, BuildStage.id)
        ).scalars()
    )


def stage_lines(
    db: Session, *, workspace_id: UUID, stage_ids: list[UUID]
) -> list[BuildStageLine]:
    if not stage_ids:
        return []
    return list(
        db.execute(
            select(BuildStageLine)
            .where(BuildStageLine.workspace_id == workspace_id)
            .where(BuildStageLine.build_stage_id.in_(stage_ids))
            .where(BuildStageLine.archived_at.is_(None))
        ).scalars()
    )


def stage_allocations(
    db: Session, *, workspace_id: UUID, build: Build, project: Project
) -> dict[UUID, dict[UUID, int]]:
    """`{stage_id: {project_entry_id: required_quantity}}` for a whole build.

    The allocation for one stage cannot be computed in isolation — the
    cumulative rounding in `_allocate` depends on every earlier stage's
    portion of the same BOM line — so this always walks the whole build.
    """
    stages = list_stages(db, workspace_id=workspace_id, build=build)
    out: dict[UUID, dict[UUID, int]] = {s.id: {} for s in stages}
    if not stages:
        return out

    stage_order = {s.id: index for index, s in enumerate(stages)}
    lines = stage_lines(db, workspace_id=workspace_id, stage_ids=[s.id for s in stages])
    portions_by_entry: dict[UUID, list[tuple[UUID, Decimal]]] = {}
    for line in lines:
        portions_by_entry.setdefault(line.project_entry_id, []).append(
            (line.build_stage_id, Decimal(line.portion_pct))
        )

    entries = project_entries_by_id(db, workspace_id=workspace_id, project=project)
    for entry_id, portions in portions_by_entry.items():
        entry = entries.get(entry_id)
        if entry is None or entry.part_id is None:
            continue
        if entry.dnp or entry.entry_type not in ("part", "meta_part"):
            continue
        part = db.get(Part, entry.part_id)
        if part is None:
            continue
        total = _required(entry, part, build.quantity)
        portions.sort(key=lambda item: stage_order.get(item[0], 0))
        for stage_id, quantity in _allocate(total, portions).items():
            out[stage_id][entry_id] = quantity
    return out


def stage_shortage(
    db: Session,
    *,
    workspace_id: UUID,
    project: Project,
    allocation: dict[UUID, int],
    portion_by_entry: dict[UUID, Decimal],
) -> list[dict]:
    """Per-stage counterpart of `service.py::shortage_analysis`.

    Same row shape, so the UI can render whole-build and per-stage shortage
    with one component; `required` is this stage's slice and `portion_pct`
    says how big that slice is.
    """
    entries = project_entries_by_id(db, workspace_id=workspace_id, project=project)

    def _order(entry_id: UUID) -> int:
        entry = entries.get(entry_id)
        return entry.order_index if entry is not None else 0

    out: list[dict] = []
    for entry_id in sorted(allocation, key=_order):
        required = allocation[entry_id]
        entry = entries.get(entry_id)
        if entry is None or entry.part_id is None:
            continue
        part = db.get(Part, entry.part_id)
        if part is None:
            continue
        available = current_quantity(db, workspace_id=workspace_id, part_id=part.id)
        sub_ids = _candidate_part_ids(db, part=part)
        sub_avail = sum(
            current_quantity(db, workspace_id=workspace_id, part_id=sid) for sid in sub_ids
        )
        out.append(
            {
                "project_entry_id": str(entry.id),
                "part_id": str(part.id),
                "part_name": part.name,
                "attrition_pct": float(entry.attrition_pct or 0),
                "portion_pct": float(portion_by_entry.get(entry_id, Decimal(0))),
                "required": required,
                "available": available,
                "substitute_ids": [str(s) for s in sub_ids],
                "substitute_available": sub_avail,
                "short_by": max(0, required - (available + sub_avail)),
            }
        )
    return out


def serialize_stage(
    stage: BuildStage, *, lines: list[BuildStageLine], shortage: list[dict]
) -> dict:
    return {
        "id": str(stage.id),
        "build_id": str(stage.build_id),
        "name": stage.name,
        "sequence": stage.sequence,
        "status": stage.status,
        "started_at": stage.started_at.isoformat() if stage.started_at else None,
        "completed_at": stage.completed_at.isoformat() if stage.completed_at else None,
        "comments": stage.comments,
        "lines": [
            {
                "id": str(line.id),
                "project_entry_id": str(line.project_entry_id),
                "portion_pct": float(line.portion_pct),
            }
            for line in lines
        ],
        "shortage": shortage,
        "created_at": stage.created_at.isoformat(),
        "updated_at": stage.updated_at.isoformat(),
    }


def stages_payload(
    db: Session, *, workspace_id: UUID, build: Build, project: Project
) -> list[dict]:
    """Every active stage of a build with its lines and per-stage shortage."""
    stages = list_stages(db, workspace_id=workspace_id, build=build)
    if not stages:
        return []
    allocations = stage_allocations(
        db, workspace_id=workspace_id, build=build, project=project
    )
    lines = stage_lines(db, workspace_id=workspace_id, stage_ids=[s.id for s in stages])
    lines_by_stage: dict[UUID, list[BuildStageLine]] = {s.id: [] for s in stages}
    for line in lines:
        lines_by_stage.setdefault(line.build_stage_id, []).append(line)

    out = []
    for stage in stages:
        stage_lines_ = lines_by_stage.get(stage.id, [])
        portion_by_entry = {
            line.project_entry_id: Decimal(line.portion_pct) for line in stage_lines_
        }
        out.append(
            serialize_stage(
                stage,
                lines=stage_lines_,
                shortage=stage_shortage(
                    db,
                    workspace_id=workspace_id,
                    project=project,
                    allocation=allocations.get(stage.id, {}),
                    portion_by_entry=portion_by_entry,
                ),
            )
        )
    return out


def create_stage(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    build: Build,
    project: Project,
    payload: BuildStageCreateIn,
) -> BuildStage:
    """Add a stage to a build. Writes no ledger rows (see rule 2 above)."""
    if build.status in ("complete", "cancelled"):
        raise BuildError(f"build is {build.status}")
    if build.archived_at is not None:
        raise BuildError("build is archived")

    existing = list_stages(db, workspace_id=workspace_id, build=build)
    entries = project_entries_by_id(db, workspace_id=workspace_id, project=project)

    seen: set[UUID] = set()
    for line in payload.lines:
        entry = entries.get(line.project_entry_id)
        if entry is None:
            raise BuildError(f"project entry {line.project_entry_id} not in this project")
        if entry.entry_type not in ("part", "meta_part") or entry.part_id is None:
            raise BuildError(f"project entry {entry.id} has no part to consume")
        if entry.dnp:
            raise BuildError(f"project entry {entry.id} is DNP")
        if entry.id in seen:
            raise BuildError(f"project entry {entry.id} listed twice in this stage")
        seen.add(entry.id)

    # A BOM line can be split across stages, but the portions must not
    # over-commit it: 60% + 60% would consume 120% of what the build needs.
    committed = _committed_portions(
        db, workspace_id=workspace_id, stage_ids=[s.id for s in existing]
    )
    for line in payload.lines:
        total = committed.get(line.project_entry_id, Decimal(0)) + line.portion_pct
        if total > Decimal(100):
            raise BuildError(
                f"project entry {line.project_entry_id} is over-committed "
                f"({total}% across stages; max 100%)"
            )

    sequence = payload.sequence
    if sequence is None:
        sequence = (max((s.sequence for s in existing), default=-1)) + 1
    elif any(s.sequence == sequence for s in existing):
        raise BuildError(f"stage sequence {sequence} already used by this build")

    stage = BuildStage(
        workspace_id=workspace_id,
        build_id=build.id,
        name=payload.name,
        sequence=sequence,
        status="planned",
        comments=payload.comments,
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(stage)
    db.flush()

    for line in payload.lines:
        db.add(
            BuildStageLine(
                workspace_id=workspace_id,
                build_stage_id=stage.id,
                project_entry_id=line.project_entry_id,
                portion_pct=line.portion_pct,
                created_by=user_id,
                updated_by=user_id,
            )
        )
    db.flush()
    return stage


def _committed_portions(
    db: Session, *, workspace_id: UUID, stage_ids: list[UUID]
) -> dict[UUID, Decimal]:
    """Portion already committed per BOM entry across a build's stages."""
    if not stage_ids:
        return {}
    rows = db.execute(
        select(
            BuildStageLine.project_entry_id,
            func.coalesce(func.sum(BuildStageLine.portion_pct), 0),
        )
        .where(BuildStageLine.workspace_id == workspace_id)
        .where(BuildStageLine.build_stage_id.in_(stage_ids))
        .where(BuildStageLine.archived_at.is_(None))
        .group_by(BuildStageLine.project_entry_id)
    )
    return {entry_id: Decimal(total) for entry_id, total in rows}


def consume_stage(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    build: Build,
    project: Project,
    stage: BuildStage,
    payload: StageConsumeIn,
) -> dict:
    """Consume one stage of a multi-stage build. All-or-nothing.

    Emits its own `build_consume` ledger rows (tagged `build_stage_id`) via
    the same `apply_consume_lines` the single-pass consume uses, so the
    demand-aggregation pre-pass, substitute validation and workspace checks
    are literally the same code.
    """
    if build.archived_at is not None:
        raise BuildError("build is archived")

    # Take the lock BEFORE the status/order checks, then re-read. Two
    # concurrent consumes of the same stage would otherwise both read
    # status='planned', both pass, and both draw stock — the per-part
    # advisory lock serialises them, but only if the decision is made
    # inside it. `lock_for_consume` locks the whole BOM, so every stage of
    # a build contends on the same set.
    lock_for_consume(
        db,
        workspace_id=workspace_id,
        project=project,
        line_part_ids=[line.part_id for line in payload.lines],
    )
    db.refresh(build)
    db.refresh(stage)

    if build.status not in ("planned", "in_progress"):
        raise BuildError(f"build is {build.status}")
    if stage.status == "complete":
        raise BuildError(f"stage '{stage.name}' is already complete")

    stages = list_stages(db, workspace_id=workspace_id, build=build)
    # Stages are ordered because a partially-built device is built in order;
    # consuming stage 3 before stage 1 would report a physically impossible
    # assembly state.
    for earlier in stages:
        if earlier.id == stage.id:
            break
        if earlier.status != "complete":
            raise BuildError(
                f"stage '{earlier.name}' (sequence {earlier.sequence}) must be "
                f"consumed before '{stage.name}'"
            )

    allocations = stage_allocations(
        db, workspace_id=workspace_id, build=build, project=project
    )
    required_by_entry = {
        entry_id: quantity
        for entry_id, quantity in allocations.get(stage.id, {}).items()
        if quantity > 0
    }
    if not required_by_entry:
        raise BuildError(f"stage '{stage.name}' has nothing to consume")

    entries_by_id = project_entries_by_id(db, workspace_id=workspace_id, project=project)

    # Release exactly this stage's slice of the whole-build reservation.
    # Reserve rows are keyed by the BOM entry's own part, so the amounts are
    # grouped by `entry.part_id` even when the operator consumes a substitute.
    release_amounts: dict[UUID, int] = {}
    for entry_id, quantity in required_by_entry.items():
        entry: ProjectEntry | None = entries_by_id.get(entry_id)
        if entry is None or entry.part_id is None:
            continue
        release_amounts[entry.part_id] = release_amounts.get(entry.part_id, 0) + quantity
    release_reservation_amounts(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        build=build,
        amounts=release_amounts,
        build_stage_id=stage.id,
    )

    now = utcnow()
    consumed_entries = apply_consume_lines(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        build=build,
        project=project,
        lines=list(payload.lines),
        entries_by_id=entries_by_id,
        required_by_entry=required_by_entry,
        now=now,
        build_stage_id=stage.id,
    )

    stage.status = "complete"
    stage.started_at = stage.started_at or now
    stage.completed_at = now
    stage.updated_by = user_id

    build.started_at = build.started_at or now
    if build.status == "planned":
        build.status = "in_progress"
    build.updated_by = user_id

    # The build closes when its last stage does. The sub-assembly output is
    # produced exactly once, here — a half-built device is not a unit of
    # output stock.
    outstanding = [s for s in stages if s.id != stage.id and s.status != "complete"]
    output_lot = output_entry = None
    if not outstanding:
        output_lot, output_entry = produce_output(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            build=build,
            project=project,
            output_storage_location_id=payload.output_storage_location_id,
            output_lot_name=payload.output_lot_name,
            now=now,
        )
        complete_build(db, build=build, user_id=user_id, output_lot=output_lot, now=now)
        # Stages whose portions sum to less than 100% leave part of the
        # up-front reservation outstanding; the build is done, so free it.
        release_reservations(db, workspace_id=workspace_id, user_id=user_id, build=build)

    log.info(
        "build stage consumed",
        extra={
            "workspace_id": str(workspace_id),
            "build_id": str(build.id),
            "build_stage_id": str(stage.id),
            "project_id": str(project.id),
            "consumed_lines": len(consumed_entries),
            "build_status": build.status,
        },
    )
    return {
        "build_id": str(build.id),
        "build_stage_id": str(stage.id),
        "stage_status": stage.status,
        "build_status": build.status,
        "consumed_entries": [str(s.id) for s in consumed_entries],
        "remaining_stages": len(outstanding),
        "output_lot_id": str(output_lot.id) if output_lot else None,
        "output_stock_entry_id": str(output_entry.id) if output_entry else None,
    }


def has_stages(db: Session, *, workspace_id: UUID, build: Build) -> bool:
    return (
        db.execute(
            select(func.count(BuildStage.id))
            .where(BuildStage.workspace_id == workspace_id)
            .where(BuildStage.build_id == build.id)
            .where(BuildStage.archived_at.is_(None))
        ).scalar_one()
        > 0
    )


def has_consumed_stage(db: Session, *, workspace_id: UUID, build: Build) -> bool:
    """True if any stage of this build has already drawn stock.

    Gate for whole-build edits (a quantity change re-derives the up-front
    reservation, which would over-reserve the already-consumed stages).
    """
    return (
        db.execute(
            select(func.count(BuildStage.id))
            .where(BuildStage.workspace_id == workspace_id)
            .where(BuildStage.build_id == build.id)
            .where(BuildStage.status.not_in(_ACTIVE_STAGE_STATUSES))
        ).scalar_one()
        > 0
    )
