"""Build / consume-from-BOM service.

A build runs against a project's BOM. Consuming the build emits
'build_consume' ledger rows for each input line and, if the project has
an associated sub-assembly part, a 'build_produce' row + a Lot tagged
source_type='build', source_build_id=build.id."""
from __future__ import annotations

from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.time import utcnow
from app.domain._quantity import QUANTITY_ZERO, quantity_out
from app.domain.builds.models import Build
from app.domain.builds.schemas import ConsumeIn, ConsumeLineIn

log = get_logger(__name__)
from app.domain.lots.models import Lot
from app.domain.parts.models import Part, PartMetaMember, PartSubstitute
from app.domain.projects.models import Project, ProjectEntry
from app.domain.stock.models import StockEntry
from app.domain.stock.service import (
    current_quantity,
    enforce_storage_constraints,
    lock_parts_for_stock_write,
    unit_for_part,
)
from app.domain.storage.models import StorageLocation


class BuildError(Exception):
    pass


def _required(entry: ProjectEntry, part: Part | None, build_qty: int) -> int:
    """Required quantity for one entry across `build_qty` builds, including
    attrition. Two attrition sources compound multiplicatively:

    * part-intrinsic loss — ``parts.attrition_percentage`` /
      ``attrition_min_quantity`` (taping pickup error, solder rejects), spec
      §19.3;
    * per-BOM-line process scrap — ``project_entries.attrition_pct``
      (Track B1), inflating what this specific line wastes.

    ``ceil(base * (1 + part_pct/100) * (1 + line_pct/100))`` but at least
    ``base + attrition_min_quantity``, where ``base = qty * builds``.

    Quantities are integer-valued (nothing in the API can write a
    fractional one yet, even though alembic 0074 widened the columns to
    ``Numeric(18,6)``), so the attrition-inflated requirement is rounded UP
    to an integer here — the single place shortage analysis, reservations,
    and consumption all read — so planning and actual consumption agree on
    the same integer. Decimal math keeps e.g. ``100 * 2.5%`` at exactly
    ``102.5 -> 103`` rather than a binary-float ``102.4999…``.

    Making the trailing ceil dimension-aware — a `count` part still ceils,
    a measured one quantizes to its unit, because you cannot round 1.5 m of
    wire up to 2 m — is a later step of the units-of-measure track, and a
    product decision rather than a mechanical one."""
    # `entry.quantity`, `part.attrition_min_quantity` and
    # `part.attrition_percentage` are all Numeric columns and arrive as
    # Decimals, which is what this function already wanted — 0074's
    # widening drops straight in with no coercion. `Decimal(entry.quantity)`
    # rather than `Decimal(int(entry.quantity))`: identical for every value
    # reachable today, but it does not discard a fraction the column can now
    # physically hold.
    base = Decimal(entry.quantity) * build_qty
    part_pct = Decimal(part.attrition_percentage) if part else Decimal(0)
    line_pct = Decimal(entry.attrition_pct or 0)
    floor_extra = part.attrition_min_quantity if part else 0
    target = base * (1 + part_pct / 100) * (1 + line_pct / 100)
    target = max(target, base + floor_extra)
    return int(target.to_integral_value(rounding=ROUND_CEILING))


def _candidate_part_ids(db: Session, *, part: Part) -> list[UUID]:
    """Parts that may be used in place of `part` for build consumption.
    For a meta-part: its registered members. For a regular part:
    registered substitutes (one-way main→sub or bidirectional)."""
    if part.part_type == "meta":
        rows = list(
            db.execute(
                select(PartMetaMember.part_id)
                .where(PartMetaMember.workspace_id == part.workspace_id)
                .where(PartMetaMember.meta_part_id == part.id)
            ).scalars()
        )
        return [r for r in rows if r != part.id]

    sub_rows = list(
        db.execute(
            select(PartSubstitute)
            .where(PartSubstitute.workspace_id == part.workspace_id)
            .where(
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
    parts) or meta-part members (for meta parts).

    `available`, `substitute_available` and `short_by` are exact
    `Decimal`s straight off the ledger — callers that serialise these rows
    must go through `shortage_rows_out`."""
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
        # `start=QUANTITY_ZERO` so a part with no substitutes yields an
        # exact Decimal zero rather than `sum()`'s int 0 — the rows below
        # are compared and subtracted downstream, and one int sneaking in
        # is how a mixed-type comparison creeps back.
        sub_avail = sum(
            (current_quantity(db, workspace_id=workspace_id, part_id=sid) for sid in sub_ids),
            QUANTITY_ZERO,
        )

        out.append(
            {
                "project_entry_id": str(e.id),
                "part_id": str(part.id),
                "part_name": part.name,
                # `required` is the effective, attrition-adjusted, ceil-rounded
                # integer demand. `attrition_pct` is surfaced so the UI can show
                # what inflated it.
                "attrition_pct": float(e.attrition_pct or 0),
                "required": required,
                "available": available,
                "substitute_ids": [str(s) for s in sub_ids],
                "substitute_available": sub_avail,
                "short_by": max(QUANTITY_ZERO, required - (available + sub_avail)),
            }
        )
    return out


#: Quantity-bearing keys of a `shortage_analysis` row. `required` is an
#: int by construction (`_required` ceils); the rest are ledger sums and
#: are therefore exact `Decimal`s.
_SHORTAGE_QUANTITY_KEYS = ("required", "available", "substitute_available", "short_by")


def shortage_rows_out(rows: list[dict]) -> list[dict]:
    """JSON-ready copies of `shortage_analysis` rows.

    `shortage_analysis` keeps its quantities as exact `Decimal`s because
    `reports/service.py` does arithmetic on them (`_can_build_now`,
    `blocking_lines_count`). Two routes put the whole row straight on the
    wire — `GET /api/builds/{id}` and `GET /api/reports/bom-shortage` —
    and an untyped `Decimal` there renders as `5.0` instead of `5`.
    (`mcp/tools/read.py::bom_shortages` reshapes the row, so it applies
    `quantity_out` to the keys it emits rather than calling this.)
    Converting at the boundary rather than inside `shortage_analysis`
    keeps the internal arithmetic exact.
    """
    return [
        {
            key: (quantity_out(value) if key in _SHORTAGE_QUANTITY_KEYS else value)
            for key, value in row.items()
        }
        for row in rows
    ]


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
    now = utcnow()
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
            unit=unit_for_part(part),
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


def _outstanding_reservations(
    db: Session, *, workspace_id: UUID, build: Build
) -> tuple[list[UUID], list[tuple[StockEntry, Decimal]]]:
    """Reserve rows for `build` and how much of each is still outstanding.

    Returns `(all_reserve_part_ids, [(reserve_row, remaining), …])`.

    `remaining` stays a `Decimal`. `stock_entries.quantity_delta` is
    `Numeric(18,6)` since alembic 0074, so an `int()` here would truncate
    any fraction the column can now physically hold — the same coercion
    0074 had to remove from `_required`. Nothing writes a fractional
    quantity today (every quantity schema above the DB is still `int`), so
    this changes no current behaviour; it just refuses to be the place that
    silently loses 0.5 of a metre when the units-of-measure track lands.

    "Outstanding" is measured in **quantity**, not row existence: a reserve
    row of 100 that has already been released by 40 (via one or more
    `operation_type='release'` rows carrying `related_entry_id == reserve.id`)
    is still outstanding for 60. Before multi-stage builds every release was
    all-or-nothing so existence and quantity agreed; per-stage consume
    releases only the slice it consumes, so the accounting has to be
    quantity-based or the next release would over-release and drive
    `reserved_quantity` negative.

    Rows are returned in a deterministic order (occurred_at, id) so two
    partial releases of the same reservation drain it in the same sequence.

    The first element is *every* reserve row's part_id — including
    fully-released ones — so callers take the same lock set they always did.
    """
    reserve_rows = list(
        db.execute(
            select(StockEntry)
            .where(StockEntry.workspace_id == workspace_id)
            .where(StockEntry.build_id == build.id)
            .where(StockEntry.status == "reserved")
            .where(StockEntry.operation_type == "reserve")
            .order_by(StockEntry.occurred_at, StockEntry.id)
        ).scalars()
    )
    if not reserve_rows:
        return [], []
    released_by_reserve: dict[UUID, Decimal] = {}
    for related_id, released in db.execute(
        select(
            StockEntry.related_entry_id,
            func.coalesce(func.sum(-StockEntry.quantity_delta), 0),
        )
        .where(StockEntry.workspace_id == workspace_id)
        .where(StockEntry.build_id == build.id)
        .where(StockEntry.status == "reserved")
        .where(StockEntry.operation_type == "release")
        .where(StockEntry.related_entry_id.is_not(None))
        .group_by(StockEntry.related_entry_id)
    ):
        released_by_reserve[related_id] = Decimal(released or 0)

    outstanding: list[tuple[StockEntry, Decimal]] = []
    for r in reserve_rows:
        remaining = Decimal(r.quantity_delta) - released_by_reserve.get(r.id, Decimal(0))
        if remaining > 0:
            outstanding.append((r, remaining))
    return [r.part_id for r in reserve_rows], outstanding


def _write_release(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    build: Build,
    reserve: StockEntry,
    quantity: Decimal | int,
    build_stage_id: UUID | None,
    now: datetime,
) -> None:
    # The release is a counter-row to `reserve`, so it inherits that row's
    # unit stamp rather than re-resolving the part's current one: the two
    # must cancel exactly, and `reserve.unit` is still the right answer
    # after a hard delete has NULLed `reserve.part_id`. The part-unit
    # immutability rule (alembic 0077) means the two can never disagree
    # while the reservation is outstanding.
    db.add(
        StockEntry(
            workspace_id=workspace_id,
            part_id=reserve.part_id,
            quantity_delta=-quantity,
            unit=reserve.unit,
            status="reserved",
            operation_type="release",
            related_entry_id=reserve.id,
            build_id=build.id,
            build_stage_id=build_stage_id,
            project_id=reserve.project_id,
            occurred_at=now,
            created_by=user_id,
        )
    )


def release_reservations(
    db: Session, *, workspace_id: UUID, user_id: UUID | None, build: Build
) -> int:
    """Write a counter-row for every outstanding reserve row tied to `build`.

    Idempotent: returns 0 if there is nothing to release. Partial releases
    written by per-stage consume are honoured — each reserve row is
    countered only for the quantity still outstanding.
    """
    all_part_ids, outstanding = _outstanding_reservations(
        db, workspace_id=workspace_id, build=build
    )
    if not all_part_ids:
        return 0
    # BE2-008: aggregate distinct part_ids from the reserve set, sort,
    # take the per-part lock before reading the release-counter set.
    # This serialises archive-while-consuming and any release flow that
    # races with another build releasing the same parts.
    lock_parts_for_stock_write(db, workspace_id=workspace_id, part_ids=all_part_ids)
    now = utcnow()
    written = 0
    for reserve, remaining in outstanding:
        _write_release(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            build=build,
            reserve=reserve,
            quantity=remaining,
            build_stage_id=None,
            now=now,
        )
        written += 1
    if written:
        db.flush()
    return written


def release_reservation_amounts(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    build: Build,
    amounts: dict[UUID, Decimal | int],
    build_stage_id: UUID | None = None,
) -> int:
    """Release exactly `amounts[part_id]` units of this build's reservations.

    Used by per-stage consume: reservations are taken **once, up front, for
    the whole build** (see `apply_reservations`), and each stage releases
    only the slice it is about to consume. Releasing the whole reservation
    on the first stage would leave later stages unreserved; writing a fresh
    reservation per stage would double-count against `reserved_quantity`.

    Reserve rows are per BOM entry and carry no `project_entry_id`, so the
    release is applied per **part** across that part's outstanding reserve
    rows (oldest first, partial on the last). That is exactly the
    granularity `stock/service.py::reserved_quantity` reads, which sums
    reserved deltas per part.

    Over-asking is clamped to what is actually outstanding, so a stage that
    consumes more than was reserved (BOM edited after the build was created)
    can never drive the reserved total negative.
    """
    all_part_ids, outstanding = _outstanding_reservations(
        db, workspace_id=workspace_id, build=build
    )
    if not all_part_ids:
        return 0
    lock_parts_for_stock_write(db, workspace_id=workspace_id, part_ids=all_part_ids)

    by_part: dict[UUID, list[tuple[StockEntry, int]]] = {}
    for reserve, remaining in outstanding:
        by_part.setdefault(reserve.part_id, []).append((reserve, remaining))

    now = utcnow()
    written = 0
    for part_id in sorted(amounts, key=str):
        want = amounts[part_id]
        for reserve, remaining in by_part.get(part_id, []):
            if want <= 0:
                break
            take = min(want, remaining)
            _write_release(
                db,
                workspace_id=workspace_id,
                user_id=user_id,
                build=build,
                reserve=reserve,
                quantity=take,
                build_stage_id=build_stage_id,
                now=now,
            )
            want -= take
            written += 1
    if written:
        db.flush()
    return written


def lock_for_consume(
    db: Session,
    *,
    workspace_id: UUID,
    project: Project,
    line_part_ids: list[UUID],
) -> None:
    """Take every lock a consume transaction needs, up front.

    Deterministic UUID-string order, before any read or write. Bundles
    together the part_ids from: the project's BOM (touched by the release
    pass), the consume lines themselves, and the optional sub-assembly
    output. Calling this here means the inner release / per-line writes /
    `output_lot` insert all acquire their lock as a re-entrant no-op
    (Postgres advisory locks are transaction-scoped). Without bundling, two
    concurrent consumes touching overlapping BOM ∪ line sets could acquire
    locks in different orders → AB/BA deadlock.

    Multi-stage consume uses the same bundle — the whole BOM, not just the
    stage's slice — so a stage consume and a whole-build consume of the same
    project can never take the two lock sets in opposite orders.
    """
    bom_part_ids = [
        e.part_id
        for e in _consumable_entries(db, workspace_id=workspace_id, project=project)
        if e.part_id is not None
    ]
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


def project_entries_by_id(
    db: Session, *, workspace_id: UUID, project: Project
) -> dict[UUID, ProjectEntry]:
    return {
        e.id: e
        for e in db.query(ProjectEntry)
        .filter(ProjectEntry.workspace_id == workspace_id, ProjectEntry.project_id == project.id)
        .all()
    }


def apply_consume_lines(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    build: Build,
    project: Project,
    lines: list[ConsumeLineIn],
    entries_by_id: dict[UUID, ProjectEntry],
    required_by_entry: dict[UUID, int],
    now: datetime,
    build_stage_id: UUID | None = None,
) -> list[StockEntry]:
    """Validate and write the `build_consume` ledger rows for `lines`.

    Shared by the whole-build consume and the per-stage consume so both go
    through the same demand-aggregation pre-pass, the same substitute /
    lot / storage validation, and the same coverage check. The only
    difference is `required_by_entry`: the whole-build path passes
    `_required(entry, part, build.quantity)` for every consumable BOM entry;
    the per-stage path passes that same number sliced by the stage's
    portions. Both therefore route through `_required` and inherit
    attrition.

    `build_stage_id` tags the emitted rows so the ledger trail shows which
    stage took what; it is None for a single-pass build.
    """
    # Pre-pass: aggregate demand per exact (part_id, lot_id, storage_location_id)
    # tuple before any per-line write. Without this, two BOM entries can each
    # claim 60 of the same 100-piece reel and each pass `current_quantity >=
    # line.quantity` independently — both lines write -60 and the lot ends up
    # at -20 (BE CRIT-3). Aggregating first means every tuple's total demand
    # is checked once, against the same per-tuple availability the per-line
    # path would have used.
    demand_by_tuple: dict[tuple[UUID, UUID | None, UUID | None], int] = {}
    for line in lines:
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
                "insufficient stock for part "
                f"{part_id} (have {quantity_out(avail)}, want {total_demand})"
            )

    # Sum requested quantity per (entry, part) so we can validate against required
    requested_by_entry: dict[UUID, int] = {}
    consumed_entries: list[StockEntry] = []

    for line in lines:
        e = entries_by_id.get(line.project_entry_id)
        if e is None:
            raise BuildError(f"project entry {line.project_entry_id} not in this project")
        if e.entry_type not in ("part", "meta_part") or e.part_id is None:
            raise BuildError(f"project entry {e.id} has no part to consume")
        if e.dnp:
            raise BuildError(f"project entry {e.id} is DNP")
        if e.id not in required_by_entry:
            # `required_by_entry` covers every consumable BOM entry for a
            # whole-build consume, so this only fires for a per-stage
            # consume: a line pointing at a BOM entry this stage does not
            # cover would otherwise slip past the coverage check and draw
            # stock the stage never planned for.
            raise BuildError(f"project entry {e.id} is not in this stage")

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
                "insufficient stock for part "
                f"{line.part_id} (have {quantity_out(avail)}, want {line.quantity})"
            )

        # `line.part_id` is already workspace-validated above — it is either
        # the BOM entry's own part or one of its registered substitutes /
        # meta members. Re-fetching it here is an identity-map hit for every
        # repeat of the same part across lines, and gives the row its unit
        # stamp (uom step 3).
        consumed_part = db.get(Part, line.part_id)

        entry_row = StockEntry(
            workspace_id=workspace_id,
            part_id=line.part_id,
            lot_id=line.lot_id,
            storage_location_id=line.storage_location_id,
            quantity_delta=-line.quantity,
            unit=unit_for_part(consumed_part),
            status="on_hand",
            operation_type="build_consume",
            build_id=build.id,
            build_stage_id=build_stage_id,
            project_id=project.id,
            occurred_at=now,
            created_by=user_id,
        )
        db.add(entry_row)
        db.flush()
        consumed_entries.append(entry_row)
        requested_by_entry[e.id] = requested_by_entry.get(e.id, 0) + line.quantity

    # Required-coverage check: every entry this pass is responsible for must
    # be covered to at least its required quantity.
    for entry_id, req in required_by_entry.items():
        e = entries_by_id.get(entry_id)
        if e is None:
            continue
        part = db.get(Part, e.part_id) if e.part_id is not None else None
        if part is None:
            continue
        got = requested_by_entry.get(entry_id, 0)
        if got < req:
            raise BuildError(
                f"entry {entry_id} ({part.name}) under-consumed (need {req}, supplied {got})"
            )

    return consumed_entries


def produce_output(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    build: Build,
    project: Project,
    output_storage_location_id: UUID | None,
    output_lot_name: str | None,
    now: datetime,
) -> tuple[Lot | None, StockEntry | None]:
    """Emit the sub-assembly output lot + `build_produce` ledger row.

    Shared by the whole-build consume and the final stage of a multi-stage
    build — a staged build produces its output exactly once, when the last
    stage completes, so the output quantity is still `build.quantity`.
    """
    output_lot: Lot | None = None
    output_entry: StockEntry | None = None
    if project.associated_subassembly_part_id is not None:
        sub_part = db.get(Part, project.associated_subassembly_part_id)
        if sub_part is None or sub_part.workspace_id != workspace_id:
            raise BuildError("project's sub-assembly part not in workspace")

        storage = None
        if output_storage_location_id is not None:
            storage = db.get(StorageLocation, output_storage_location_id)
            if storage is None or storage.workspace_id != workspace_id:
                raise BuildError("output storage not in workspace")
            if storage.archived_at is not None or storage.is_full:
                raise BuildError("output storage archived or full")
            # BE-004 follow-up (#280): the build-output StockEntry is a
            # producer write that must respect single_part_only /
            # existing_parts_only. The per-(workspace, sub_part) advisory
            # lock was already taken in the bundled lock call earlier in
            # consume(); the helper additionally acquires the per-storage
            # lock to close the cross-part race. StockConflictError is
            # mapped to 409 in routes/builds.py.
            enforce_storage_constraints(
                db, workspace_id=workspace_id, storage=storage, part_id=sub_part.id
            )

        output_lot = Lot(
            workspace_id=workspace_id,
            part_id=sub_part.id,
            name=output_lot_name or f"{build.name}-out",
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
            unit=unit_for_part(sub_part),
            status="on_hand",
            operation_type="build_produce",
            build_id=build.id,
            project_id=project.id,
            occurred_at=now,
            created_by=user_id,
        )
        db.add(output_entry)
        db.flush()

    return output_lot, output_entry


def complete_build(
    db: Session,
    *,
    build: Build,
    user_id: UUID | None,
    output_lot: Lot | None,
    now: datetime,
) -> None:
    """Close a build: status, timestamps and the output-lot back-reference.

    `output_lot_id` is only ever written here and in nothing else — see
    "Things to never do" in `docs/domain/builds-and-bom.md`.
    """
    build.status = "complete"
    build.started_at = build.started_at or now
    build.completed_at = now
    build.output_lot_id = output_lot.id if output_lot else None
    build.updated_by = user_id


def consume(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    build: Build,
    project: Project,
    payload: ConsumeIn,
) -> dict:
    """Apply a build's whole-BOM consumption plan in one pass.

    All-or-nothing within the request. This is the single-pass path that
    predates multi-stage builds and is unchanged by them; a build that has
    stages is consumed one stage at a time via `builds/stages.py::consume_stage`
    instead (the route refuses this endpoint for staged builds so the two
    can never double-consume the same BOM).
    """
    if build.status not in ("planned", "in_progress"):
        raise BuildError(f"build is {build.status}")

    lock_for_consume(
        db,
        workspace_id=workspace_id,
        project=project,
        line_part_ids=[line.part_id for line in payload.lines],
    )

    # Release any outstanding reservations first so the consumption itself
    # doesn't get double-counted against on_hand+reserved.
    release_reservations(db, workspace_id=workspace_id, user_id=user_id, build=build)

    entries_by_id = project_entries_by_id(db, workspace_id=workspace_id, project=project)

    # Every consumable BOM entry must be covered by this single pass.
    required_by_entry: dict[UUID, int] = {}
    for e in _consumable_entries(db, workspace_id=workspace_id, project=project):
        part = db.get(Part, e.part_id)
        if part is None:
            continue
        required_by_entry[e.id] = _required(e, part, build.quantity)

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
    )

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
