"""Stock ledger service. Append-only — current_stock is always SUM(quantity_delta)."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Iterable
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.domain._quantity import QUANTITY_ZERO, as_quantity, quantity_out
from app.domain.lots.models import Lot
from app.domain.parts.models import Part
from app.domain.stock.models import StockEntry
from app.domain.stock.schemas import (
    AddStockIn,
    AdjustStockIn,
    MoveStockIn,
    RemoveStockIn,
)
from app.domain.storage.models import StorageLocation
from app.domain.workspaces.models import Workspace


class StockError(Exception):
    pass


class StockConflictError(StockError):
    """Raised when a storage-location constraint blocks a stock mutation.

    Attributes:
        constraint: "single_part_only" | "existing_parts_only"
        storage_location_id: the UUID of the conflicting location
    """

    def __init__(self, message: str, *, constraint: str, storage_location_id: UUID) -> None:
        super().__init__(message)
        self.constraint = constraint
        self.storage_location_id = storage_location_id



def _belongs(obj, workspace_id: UUID) -> bool:
    return obj is not None and obj.workspace_id == workspace_id


def _lock_for_stock_write(db: Session, *, workspace_id: UUID, part_id: UUID) -> None:
    """Serialise concurrent stock writes on the same (workspace, part).

    The append-only ledger has a TOCTOU race: two concurrent operators
    consuming from the same lot both read available=N from
    `current_quantity()`, both pass `qty <= available`, then both insert
    a -qty row — the lot ends up at -qty (BE CRIT-1 / BE2-001).

    `pg_advisory_xact_lock` takes a session-level lock keyed on a
    bigint hash of (workspace_id, part_id). The lock is released
    automatically at COMMIT or ROLLBACK. Two transactions targeting
    the same tuple serialise: the second waits for the first to
    finish, then re-reads `current_quantity` against the updated
    state. The 0013 trigger is the database-side fall-back if the
    lock is ever bypassed (e.g. raw SQL outside the service layer).

    Acquired by every mutating ledger entry — producer (add / receive /
    build_produce) and consumer (remove / move / adjust / build_consume
    / reservation release). The original PR #11 omitted the producer
    side on the reasoning that "positive deltas can't go negative", but
    BE2-001 surfaced two real consequences:
      - producer/consumer races observe inconsistent intermediate
        balances when invariant checks (single_part_only, default-
        storage-mandatory) read DB state without holding the lock.
      - the 0013 trigger fires after every insert; a producer skipping
        the lock can race with a consumer and turn a controllable 4xx
        into an uncaught 500 from the trigger.
    Defence in depth on the producer is cheap (~ms) and removes both.
    """
    # UUID's __str__ is stable RFC 4122 canonical form, so the same
    # (workspace_id, part_id) pair always hashes to the same int8 lock id.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"{workspace_id}:{part_id}"},
    )


def lock_parts_for_stock_write(
    db: Session, *, workspace_id: UUID, part_ids: Iterable[UUID]
) -> None:
    """Take the per-(workspace, part) advisory lock for a set of parts
    in deterministic UUID-string order. Used by routes that mutate
    several parts in one transaction — `builds.consume`,
    `builds.apply_reservations` / `release_reservations`, `orders.receive`.

    Deterministic ordering is what prevents AB/BA deadlocks: two
    concurrent transactions touching the same set of parts acquire
    locks in the same sequence, so the second waits cleanly rather
    than circular-waiting. UUID string sort works because two
    transactions seeing the same set of UUIDs always produce the same
    sorted list."""
    for pid in sorted(set(part_ids), key=str):
        _lock_for_stock_write(db, workspace_id=workspace_id, part_id=pid)


def _lock_for_storage_constraint(
    db: Session, *, workspace_id: UUID, storage_location_id: UUID
) -> None:
    """Per-(workspace, storage) advisory lock for storage-constraint reads.

    The per-(workspace, part) lock from ``_lock_for_stock_write`` only
    serialises writers that touch the *same* part. ``single_part_only``
    and ``existing_parts_only`` are cross-part invariants on the
    *destination location*: two concurrent ``add_stock`` / ``move_stock``
    calls for **different parts** targeting the same single_part_only
    bin each hold a different per-part lock, both read
    ``stock_for_storage(loc) == []``, both pass the check, and both
    insert their StockEntry — leaving the bin holding two parts.

    This second lock closes the cross-part race by serialising every
    write whose destination is the same location. There is no DB-level
    CHECK or trigger backing single_part_only / existing_parts_only
    (the 0013 trigger only enforces non-negative balances), so the
    application-level lock is the only line of defence.

    **Lock ordering**: callers always acquire the per-part lock first
    (``_lock_for_stock_write``) and the per-storage lock second. Two
    concurrent writers targeting the same destination thus contend on
    the storage lock without circular waits — there is no path that
    holds the storage lock while waiting for a per-part lock. (The
    multi-part helpers — ``builds.consume``, ``orders.receive`` — do
    not pass a destination through ``enforce_storage_constraints``,
    so they don't take a storage lock.)
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"{workspace_id}:storage:{storage_location_id}"},
    )


def current_quantity(
    db: Session,
    *,
    workspace_id: UUID,
    part_id: UUID,
    storage_location_id: UUID | None = None,
    lot_id: UUID | None = None,
    status: str = "on_hand",
    bucket_match: bool = False,
) -> Decimal:
    """On-hand sum of quantity_delta. Two interpretations of `None`:

    - `bucket_match=False` (default, used by global / report queries):
      `None` means "don't filter on this dimension" — aggregates across
      every value of `storage_location_id` / `lot_id` for the part.

    - `bucket_match=True` (used by mutate validators): `None` means
      "match the SQL NULL bucket specifically", using `IS NULL`. Aligns
      with the 0013 `check_stock_nonneg` trigger which groups by
      `IS NOT DISTINCT FROM NEW.lot_id` / `IS NOT DISTINCT FROM
      NEW.storage_location_id` (NULL is a distinct bucket, not a
      wildcard).

    Without the explicit `bucket_match`, the validator and the trigger
    diverge whenever a caller omits `lot_id` or `storage_location_id`
    against a part whose stock lives in a non-NULL bucket: the
    validator says "you have N globally" → request passes → trigger
    fires on the NULL-bucket sum (= 0) → `check_violation` → API
    surfaces 500. This is BE-002 / DB-002 in the v2 teardown.

    Returns an exact `Decimal`. `quantity_delta` is `Numeric(18,6)` since
    migration 0074, and `SUM()` over `NUMERIC` is exact and
    order-independent — the property the whole ledger model rests on,
    since the balance is re-summed from scratch on every read. Truncating
    the sum back to `int` here (which this did until uom step 2) would
    make every roll-up in the app silently drop the fraction the column
    can now hold. Callers that need an integer — a whole-board count, a
    distributor package count — round explicitly at their own boundary,
    and callers that put the value on the wire go through
    `_quantity.quantity_out`.
    """
    q = (
        select(func.coalesce(func.sum(StockEntry.quantity_delta), 0))
        .where(StockEntry.workspace_id == workspace_id)
        .where(StockEntry.part_id == part_id)
        .where(StockEntry.status == status)
    )
    if bucket_match:
        if storage_location_id is None:
            q = q.where(StockEntry.storage_location_id.is_(None))
        else:
            q = q.where(StockEntry.storage_location_id == storage_location_id)
        if lot_id is None:
            q = q.where(StockEntry.lot_id.is_(None))
        else:
            q = q.where(StockEntry.lot_id == lot_id)
    else:
        if storage_location_id is not None:
            q = q.where(StockEntry.storage_location_id == storage_location_id)
        if lot_id is not None:
            q = q.where(StockEntry.lot_id == lot_id)
    return as_quantity(db.execute(q).scalar_one())


def bulk_current_quantities(
    db: Session,
    *,
    workspace_id: UUID,
    part_ids: list[UUID],
    status: str = "on_hand",
) -> dict[UUID, Decimal]:
    """Per-part SUM(quantity_delta) for many parts in one query.

    Used by report/listing routes that need current stock for a slice of
    parts and previously fired one `current_quantity()` call per row.
    Returns a dict keyed by part_id; parts with no rows in the
    requested `status` aren't keyed (caller should treat missing as 0).

    Single SQL query — `WHERE part_id = ANY(:part_ids) GROUP BY part_id`.
    Replacing N round-trips with one is what BE2-005 is asking for: the
    handful of ad-hoc `SELECT part_id, SUM(...) GROUP BY part_id` blocks
    in `reports.py` all funnel through here so the invariant ("current
    quantity is always sum of deltas") has exactly one expression in
    code.
    """
    if not part_ids:
        return {}
    rows = db.execute(
        select(
            StockEntry.part_id,
            func.coalesce(func.sum(StockEntry.quantity_delta), 0),
        )
        .where(StockEntry.workspace_id == workspace_id)
        .where(StockEntry.status == status)
        .where(StockEntry.part_id.in_(part_ids))
        .group_by(StockEntry.part_id)
    ).all()
    return {row[0]: as_quantity(row[1]) for row in rows}


def bulk_current_quantities_by_lot(
    db: Session,
    *,
    workspace_id: UUID,
    lot_ids: list[UUID] | None = None,
    status: str = "on_hand",
) -> dict[UUID, Decimal]:
    """Per-lot SUM(quantity_delta), keyed by lot_id.

    `lot_ids=None` aggregates every lot in the workspace (used by reports
    like stock-value / expiring-lots which scan all lots). Returns a
    dict; missing lot_ids return 0 by convention. Lots with NULL ids
    are dropped — the workspace-level "no-lot" pseudo-bucket isn't
    addressable by lot_id.
    """
    q = (
        select(
            StockEntry.lot_id,
            func.coalesce(func.sum(StockEntry.quantity_delta), 0),
        )
        .where(StockEntry.workspace_id == workspace_id)
        .where(StockEntry.status == status)
        .where(StockEntry.lot_id.is_not(None))
        .group_by(StockEntry.lot_id)
    )
    if lot_ids is not None:
        if not lot_ids:
            return {}
        q = q.where(StockEntry.lot_id.in_(lot_ids))
    rows = db.execute(q).all()
    return {row[0]: as_quantity(row[1]) for row in rows}


def stock_summary_for_part(
    db: Session, *, workspace_id: UUID, part_id: UUID, status: str = "on_hand"
) -> list[dict]:
    """Per-(storage, lot) breakdown of current stock for a part.

    Each row's `quantity` is an exact `Decimal`; rows that net to zero are
    dropped. Route serialisers put these on the wire through
    `_quantity.quantity_out`.
    """
    rows = db.execute(
        select(
            StockEntry.storage_location_id,
            StockEntry.lot_id,
            func.coalesce(func.sum(StockEntry.quantity_delta), 0).label("qty"),
        )
        .where(StockEntry.workspace_id == workspace_id)
        .where(StockEntry.part_id == part_id)
        .where(StockEntry.status == status)
        .group_by(StockEntry.storage_location_id, StockEntry.lot_id)
    ).all()
    return [
        {"storage_location_id": storage_id, "lot_id": lot_id, "quantity": qty}
        for storage_id, lot_id, raw in rows
        if (qty := as_quantity(raw)) != QUANTITY_ZERO
    ]


def total_for_part(
    db: Session, *, workspace_id: UUID, part_id: UUID, status: str = "on_hand"
) -> Decimal:
    return current_quantity(db, workspace_id=workspace_id, part_id=part_id, status=status)


def reserved_quantity(db: Session, *, workspace_id: UUID, part_id: UUID) -> Decimal:
    """Net quantity reserved (planned but not consumed) for a part. Reserve
    rows add positive deltas; release rows add negatives so an equivalent
    release brings the total back to zero."""
    q = (
        select(func.coalesce(func.sum(StockEntry.quantity_delta), 0))
        .where(StockEntry.workspace_id == workspace_id)
        .where(StockEntry.part_id == part_id)
        .where(StockEntry.status == "reserved")
    )
    return as_quantity(db.execute(q).scalar_one())


def available_quantity(db: Session, *, workspace_id: UUID, part_id: UUID) -> Decimal:
    """On-hand stock minus what is reserved for planned builds."""
    return (
        current_quantity(db, workspace_id=workspace_id, part_id=part_id)
        - reserved_quantity(db, workspace_id=workspace_id, part_id=part_id)
    )


def stock_for_storage(
    db: Session, *, workspace_id: UUID, storage_location_id: UUID, status: str = "on_hand"
) -> list[dict]:
    rows = db.execute(
        select(
            StockEntry.part_id,
            StockEntry.lot_id,
            func.coalesce(func.sum(StockEntry.quantity_delta), 0).label("qty"),
        )
        .where(StockEntry.workspace_id == workspace_id)
        .where(StockEntry.storage_location_id == storage_location_id)
        .where(StockEntry.status == status)
        .group_by(StockEntry.part_id, StockEntry.lot_id)
    ).all()
    return [
        {"part_id": part_id, "lot_id": lot_id, "quantity": qty}
        for part_id, lot_id, raw in rows
        if (qty := as_quantity(raw)) != QUANTITY_ZERO
    ]


def enforce_storage_constraints(
    db: Session,
    *,
    workspace_id: UUID,
    storage: StorageLocation,
    part_id: UUID,
) -> None:
    """Enforce single_part_only and existing_parts_only constraints using
    current stock (positive on-hand balances), not historical row counts.

    Public callers (``add_stock``, ``move_stock``, ``orders.receive``,
    ``builds.consume``) all acquire the per-(workspace, part) advisory
    lock via ``_lock_for_stock_write`` / ``lock_parts_for_stock_write``
    before calling this helper — that lock must be held when this runs.
    We additionally acquire the per-(workspace, storage) lock here so
    that two concurrent writers targeting the *same destination* with
    *different parts* can't both pass the single_part_only /
    existing_parts_only check on a stale read and then both insert
    (cross-part race). The per-part lock alone only serialises same-part
    writers; the storage lock closes the cross-part hole. Lock ordering
    is fixed (part, then storage) to avoid AB/BA deadlocks with
    ``_lock_for_stock_write``.

    Scope: every producer path whose destination is a constrained
    location — ``add_stock``, ``move_stock`` (destination side),
    ``orders.receive`` (each receive line) and ``builds.consume``
    (sub-assembly output). ``adjust_stock`` / ``remove_stock`` don't
    enter constrained locations from outside.

    Raises StockConflictError on violation.
    """
    _lock_for_storage_constraint(db, workspace_id=workspace_id, storage_location_id=storage.id)
    db.refresh(storage)

    if storage.single_part_only:
        # Any part other than the one being added/moved-in currently has
        # positive on-hand stock at this location?
        current = stock_for_storage(db, workspace_id=workspace_id, storage_location_id=storage.id)
        other_parts = {row["part_id"] for row in current if row["part_id"] != part_id}
        if other_parts:
            raise StockConflictError(
                "destination is single-part-only and already holds a different part",
                constraint="single_part_only",
                storage_location_id=storage.id,
            )

    if storage.existing_parts_only:
        # The part must have at least one prior positive stock-entry at this
        # location (i.e. it was previously stocked here).
        prior = db.execute(
            select(func.count())
            .select_from(StockEntry)
            .where(StockEntry.workspace_id == workspace_id)
            .where(StockEntry.storage_location_id == storage.id)
            .where(StockEntry.part_id == part_id)
            .where(StockEntry.quantity_delta > 0)
        ).scalar_one()
        if not prior:
            raise StockConflictError(
                "destination only accepts previously-stocked parts and this part has no prior stock here",
                constraint="existing_parts_only",
                storage_location_id=storage.id,
            )


def validate_storage_constraint_flag_update(
    db: Session,
    *,
    workspace_id: UUID,
    storage: StorageLocation,
    requested_single_part_only: bool | None = None,
    requested_existing_parts_only: bool | None = None,
) -> None:
    """Reject flag enables that conflict with current on-hand contents."""
    enable_single = requested_single_part_only is True and not storage.single_part_only
    enable_existing = requested_existing_parts_only is True and not storage.existing_parts_only
    if not enable_single and not enable_existing:
        return

    _lock_for_storage_constraint(db, workspace_id=workspace_id, storage_location_id=storage.id)
    db.refresh(storage)
    enable_single = requested_single_part_only is True and not storage.single_part_only
    enable_existing = requested_existing_parts_only is True and not storage.existing_parts_only
    if not enable_single and not enable_existing:
        return

    current_rows = stock_for_storage(db, workspace_id=workspace_id, storage_location_id=storage.id)
    current_part_ids = {row["part_id"] for row in current_rows if row["quantity"] > 0}

    if enable_single and len(current_part_ids) > 1:
        raise StockConflictError(
            "storage cannot be marked single-part-only while it holds multiple parts",
            constraint="single_part_only",
            storage_location_id=storage.id,
        )

    if enable_existing and current_part_ids:
        prior_part_ids = set(
            db.execute(
                select(StockEntry.part_id)
                .where(StockEntry.workspace_id == workspace_id)
                .where(StockEntry.storage_location_id == storage.id)
                .where(StockEntry.part_id.in_(current_part_ids))
                .where(StockEntry.quantity_delta > 0)
                .distinct()
            ).scalars()
        )
        if current_part_ids - prior_part_ids:
            raise StockConflictError(
                "storage cannot be marked existing-parts-only while it holds a part "
                "with no prior positive entry",
                constraint="existing_parts_only",
                storage_location_id=storage.id,
            )


def add_stock(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    payload: AddStockIn,
) -> StockEntry:
    # Producer-side advisory lock (BE2-001). Held for the rest of the
    # transaction; serialises concurrent producer/consumer writes on
    # the same (workspace, part) so invariant reads (default-storage,
    # serial-tracking, single_part_only) and the StockEntry insert are
    # not interleaved with a remove/move/adjust on another connection.
    _lock_for_stock_write(db, workspace_id=workspace_id, part_id=payload.part_id)
    part = db.get(Part, payload.part_id)
    if not _belongs(part, workspace_id):
        raise StockError("part not found")
    storage = None
    if payload.storage_location_id:
        storage = db.get(StorageLocation, payload.storage_location_id)
        if not _belongs(storage, workspace_id):
            raise StockError("storage location not found")
        if storage.archived_at is not None:
            raise StockError("storage location is archived")
        if storage.is_full:
            raise StockError("storage location is marked full")
        enforce_storage_constraints(
            db, workspace_id=workspace_id, storage=storage, part_id=payload.part_id
        )

    # Mandatory default-storage check (spec §19.2). Previously the chain
    # short-circuited when `storage` was None — any row that simply omitted
    # `storage_location_id` would land with NULL storage even on a part
    # flagged default_storage_mandatory. The bulk-import-from-scan flow
    # exploited this implicitly by accepting rows with no storage. Now we
    # also reject the omitted-storage case (BE CRIT-2 / Sec audit).
    if part.default_storage_mandatory and part.default_storage_location_id:
        if storage is None or storage.id != part.default_storage_location_id:
            raise StockError("part requires default storage location")

    # Serial-tracking enforcement: when the workspace has serial tracking on
    # AND the part is flagged serialized, every stock addition must produce
    # exactly one serialized lot (quantity=1, serial_number required).
    ws = db.get(Workspace, workspace_id)
    if ws is not None and ws.serial_tracking_enabled and part.serialized:
        if payload.quantity != 1:
            raise StockError("serialized parts must be added one at a time (quantity=1)")
        if not payload.lot or not (payload.lot.serial_number or "").strip():
            raise StockError("serialized parts require lot.serial_number")

    unit_price: Decimal | None = None
    currency: str | None = None
    if payload.price and payload.price.mode != "none":
        currency = payload.price.currency
        if payload.price.mode == "per_component" and payload.price.unit_price is not None:
            unit_price = payload.price.unit_price
        elif payload.price.mode == "entire_lot" and payload.price.total_price is not None:
            unit_price = payload.price.total_price / payload.quantity

    lot: Lot | None = None
    if payload.lot is not None or unit_price is not None:
        exp: date | None = None
        if payload.lot and payload.lot.expiration_date:
            try:
                exp = date.fromisoformat(payload.lot.expiration_date)
            except ValueError as e:
                raise StockError("invalid expiration_date") from e
        lot = Lot(
            workspace_id=workspace_id,
            part_id=part.id,
            name=(payload.lot.name if payload.lot else None),
            comments=(payload.lot.comments if payload.lot else None),
            expiration_date=exp,
            serial_number=(payload.lot.serial_number if payload.lot else None),
            source_type="manual",
            purchase_quantity=payload.quantity,
            purchase_unit_cost=unit_price,
            purchase_currency=currency,
            created_by=user_id,
            updated_by=user_id,
        )
        db.add(lot)
        db.flush()

    entry = StockEntry(
        workspace_id=workspace_id,
        part_id=part.id,
        lot_id=(lot.id if lot else None),
        storage_location_id=(storage.id if storage else None),
        quantity_delta=payload.quantity,
        status="on_hand",
        unit_price=unit_price,
        currency=currency,
        operation_type="add",
        comments=payload.comments,
        bag_signature=payload.bag_signature,
        occurred_at=utcnow(),
        created_by=user_id,
    )
    db.add(entry)
    db.flush()
    return entry


def remove_stock(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    payload: RemoveStockIn,
) -> StockEntry:
    _lock_for_stock_write(db, workspace_id=workspace_id, part_id=payload.part_id)
    part = db.get(Part, payload.part_id)
    if not _belongs(part, workspace_id):
        raise StockError("part not found")
    # Validate caller-supplied FK targets against the workspace BEFORE the
    # availability check. current_quantity is workspace-filtered, so a
    # foreign lot/storage is masked as "0 available" — that's defense in
    # depth, not a contract. Validating here also keeps the failure mode
    # explicit ("lot not found", not "insufficient stock").
    if payload.storage_location_id is not None:
        storage = db.get(StorageLocation, payload.storage_location_id)
        if not _belongs(storage, workspace_id):
            raise StockError("storage location not found")
    if payload.lot_id is not None:
        lot = db.get(Lot, payload.lot_id)
        if not _belongs(lot, workspace_id):
            raise StockError("lot not found")
    available = current_quantity(
        db,
        workspace_id=workspace_id,
        part_id=part.id,
        storage_location_id=payload.storage_location_id,
        lot_id=payload.lot_id,
        bucket_match=True,
    )
    if payload.quantity > available:
        raise StockError(
            f"insufficient stock (have {quantity_out(available)}, want {payload.quantity})"
        )
    entry = StockEntry(
        workspace_id=workspace_id,
        part_id=part.id,
        lot_id=payload.lot_id,
        storage_location_id=payload.storage_location_id,
        quantity_delta=-payload.quantity,
        status="on_hand",
        operation_type="remove",
        comments=payload.comments,
        occurred_at=utcnow(),
        created_by=user_id,
    )
    db.add(entry)
    db.flush()
    return entry


def _prepare_move(
    db: Session,
    *,
    workspace_id: UUID,
    part_id: UUID,
    quantity: Decimal,
    source_storage_location_id: UUID | None,
    source_lot_id: UUID | None,
    destination_storage_location_id: UUID,
) -> StorageLocation:
    """Lock, validate and availability-check a move; return the destination.

    Every move — the schema-driven ``move_stock`` and the Decimal-typed
    ``move_quantity`` kitting uses — goes through here, so the per-part
    advisory lock, the workspace checks on both ends, the bucket-matched
    availability check and the destination-constraint enforcement have
    exactly one implementation.
    """
    _lock_for_stock_write(db, workspace_id=workspace_id, part_id=part_id)
    part = db.get(Part, part_id)
    if not _belongs(part, workspace_id):
        raise StockError("part not found")
    dest = db.get(StorageLocation, destination_storage_location_id)
    if not _belongs(dest, workspace_id):
        raise StockError("destination not found")
    if dest.archived_at is not None:
        raise StockError("destination is archived")
    if dest.is_full:
        raise StockError("destination is full")

    # Validate caller-supplied source FKs against the workspace. Same
    # defense-in-depth rationale as remove_stock — current_quantity ws-filter
    # would mask a foreign source as "0 available" but the failure should
    # name the real cause.
    if source_storage_location_id is not None:
        src_storage = db.get(StorageLocation, source_storage_location_id)
        if not _belongs(src_storage, workspace_id):
            raise StockError("source storage not found")
    if source_lot_id is not None:
        # For a split-lot move the same id is re-fetched by the caller for
        # the new-lot copy; this still validates the workspace boundary up
        # front so a foreign source-lot fails with a clear message.
        src_lot = db.get(Lot, source_lot_id)
        if not _belongs(src_lot, workspace_id):
            raise StockError("source lot not found")

    # `current_quantity` returns an exact `Decimal` (uom step 2), so this
    # comparison keeps whatever precision the ledger hands back — the
    # `Decimal(...)` wrapper #900 put here in anticipation is no longer
    # needed. `quantity_out` on the message because the raw value would
    # otherwise read "have 6.000000" in a 4xx body the route re-raises.
    available = current_quantity(
        db,
        workspace_id=workspace_id,
        part_id=part_id,
        storage_location_id=source_storage_location_id,
        lot_id=source_lot_id,
        bucket_match=True,
    )
    if quantity > available:
        raise StockError(
            "insufficient stock at source "
            f"(have {quantity_out(available)}, want {quantity_out(quantity)})"
        )

    enforce_storage_constraints(db, workspace_id=workspace_id, storage=dest, part_id=part_id)
    return dest


def _write_move_pair(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    part_id: UUID,
    quantity: Decimal,
    source_storage_location_id: UUID | None,
    source_lot_id: UUID | None,
    dest_storage_location_id: UUID,
    dest_lot_id: UUID | None,
    comments: str | None,
    build_id: UUID | None = None,
    build_stage_id: UUID | None = None,
) -> tuple[StockEntry, StockEntry]:
    """Write the matched ``move_out`` / ``move_in`` pair for one move.

    Circular FK on `related_entry_id`: each row's FK points at the other.
    PG enforces FK on INSERT (constraints aren't DEFERRABLE in the schema),
    and SA's topological sort can't break the cycle, so `add_all([out, in])`
    always violates one direction. Fix is the classic three-step write under
    a savepoint so the back-pointer update commits atomically with the
    inserts and an outside transaction never observes a dangling
    back-pointer (BE2-007).

    `build_id` / `build_stage_id` are set only by kitting (Track B3), which
    is a build-driven move; they put the kit's relocation rows on
    `GET /api/builds/{id}/activity` next to the build's own consume rows.
    """
    # Pre-assign UUIDs so the two rows can reference each other.
    out_id = uuid.uuid4()
    in_id = uuid.uuid4()
    now = utcnow()
    with db.begin_nested():
        out_entry = StockEntry(
            id=out_id,
            workspace_id=workspace_id,
            part_id=part_id,
            lot_id=source_lot_id,
            storage_location_id=source_storage_location_id,
            quantity_delta=-quantity,
            status="on_hand",
            operation_type="move_out",
            related_entry_id=None,  # set after the IN row exists
            build_id=build_id,
            build_stage_id=build_stage_id,
            comments=comments,
            occurred_at=now,
            created_by=user_id,
        )
        db.add(out_entry)
        db.flush()
        in_entry = StockEntry(
            id=in_id,
            workspace_id=workspace_id,
            part_id=part_id,
            lot_id=dest_lot_id,
            storage_location_id=dest_storage_location_id,
            quantity_delta=quantity,
            status="on_hand",
            operation_type="move_in",
            related_entry_id=out_id,
            build_id=build_id,
            build_stage_id=build_stage_id,
            comments=comments,
            occurred_at=now,
            created_by=user_id,
        )
        db.add(in_entry)
        db.flush()
        out_entry.related_entry_id = in_id
        db.flush()
    return out_entry, in_entry


def move_quantity(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    part_id: UUID,
    quantity: Decimal,
    source_storage_location_id: UUID | None,
    source_lot_id: UUID | None,
    destination_storage_location_id: UUID,
    comments: str | None = None,
    build_id: UUID | None = None,
    build_stage_id: UUID | None = None,
) -> tuple[StockEntry, StockEntry]:
    """Move `quantity` of one part between locations, Decimal end-to-end.

    The schema-free sibling of `move_stock`, for in-process callers that
    already hold `Decimal` quantities — kitting (Track B3) is the first.
    Routing through `MoveStockIn` instead would push every quantity through
    an `int` pydantic field, which is precisely the truncating coercion
    alembic 0074 widened the columns to avoid.

    No `split_lot`: a kit relocates a bag, it does not subdivide a lot, so
    the moved rows keep the source lot id and the lot's cost basis stays
    with the physical material.
    """
    _prepare_move(
        db,
        workspace_id=workspace_id,
        part_id=part_id,
        quantity=quantity,
        source_storage_location_id=source_storage_location_id,
        source_lot_id=source_lot_id,
        destination_storage_location_id=destination_storage_location_id,
    )
    return _write_move_pair(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        part_id=part_id,
        quantity=quantity,
        source_storage_location_id=source_storage_location_id,
        source_lot_id=source_lot_id,
        dest_storage_location_id=destination_storage_location_id,
        dest_lot_id=source_lot_id,
        comments=comments,
        build_id=build_id,
        build_stage_id=build_stage_id,
    )


def move_stock(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    payload: MoveStockIn,
) -> tuple[StockEntry, StockEntry]:
    quantity = Decimal(payload.quantity)
    dest = _prepare_move(
        db,
        workspace_id=workspace_id,
        part_id=payload.part_id,
        quantity=quantity,
        source_storage_location_id=payload.source_storage_location_id,
        source_lot_id=payload.source_lot_id,
        destination_storage_location_id=payload.destination_storage_location_id,
    )

    if payload.split_lot and payload.source_lot_id is not None:
        # Wrap the lot creation + the matched stock writes in a savepoint
        # so a downstream raise (e.g. the trigger noticing inconsistency
        # on the IN row) cleans up the dangling lot rather than leaving
        # an orphan parented at `src_lot.id`.
        src_lot = db.get(Lot, payload.source_lot_id)
        if not _belongs(src_lot, workspace_id):
            raise StockError("source lot not found")
        with db.begin_nested():
            new_lot = Lot(
                workspace_id=workspace_id,
                part_id=payload.part_id,
                name=f"{src_lot.name or 'lot'}-split",
                parent_lot_id=src_lot.id,
                description=src_lot.description,
                comments=f"split from {src_lot.id}",
                expiration_date=src_lot.expiration_date,
                source_type="split",
                purchase_quantity=payload.quantity,
                purchase_unit_cost=src_lot.purchase_unit_cost,
                purchase_currency=src_lot.purchase_currency,
                created_by=user_id,
                updated_by=user_id,
            )
            db.add(new_lot)
            db.flush()
            pair = _write_move_pair(
                db,
                workspace_id=workspace_id,
                user_id=user_id,
                part_id=payload.part_id,
                quantity=quantity,
                source_storage_location_id=payload.source_storage_location_id,
                source_lot_id=payload.source_lot_id,
                dest_storage_location_id=dest.id,
                dest_lot_id=new_lot.id,
                comments=payload.comments,
            )
        return pair

    return _write_move_pair(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        part_id=payload.part_id,
        quantity=quantity,
        source_storage_location_id=payload.source_storage_location_id,
        source_lot_id=payload.source_lot_id,
        dest_storage_location_id=dest.id,
        dest_lot_id=payload.source_lot_id,
        comments=payload.comments,
    )


def adjust_stock(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    payload: AdjustStockIn,
) -> StockEntry | None:
    _lock_for_stock_write(db, workspace_id=workspace_id, part_id=payload.part_id)
    part = db.get(Part, payload.part_id)
    if not _belongs(part, workspace_id):
        raise StockError("part not found")
    # Active leak fix: adjust writes a positive delta = (actual_quantity - 0)
    # when the (lot, storage) tuple resolves to 0 stock — which is exactly
    # what foreign FKs would return through current_quantity's ws filter.
    # Without this validation, an A-workspace caller can persist a positive
    # StockEntry in workspace A whose lot_id / storage_location_id point at
    # workspace B's rows.
    if payload.storage_location_id is not None:
        storage = db.get(StorageLocation, payload.storage_location_id)
        if not _belongs(storage, workspace_id):
            raise StockError("storage location not found")
    if payload.lot_id is not None:
        lot = db.get(Lot, payload.lot_id)
        if not _belongs(lot, workspace_id):
            raise StockError("lot not found")
    current = current_quantity(
        db,
        workspace_id=workspace_id,
        part_id=part.id,
        storage_location_id=payload.storage_location_id,
        lot_id=payload.lot_id,
        bucket_match=True,
    )
    delta = payload.actual_quantity - current
    if delta == 0:
        return None
    entry = StockEntry(
        workspace_id=workspace_id,
        part_id=part.id,
        lot_id=payload.lot_id,
        storage_location_id=payload.storage_location_id,
        quantity_delta=delta,
        status="on_hand",
        operation_type="adjust",
        comments=payload.comments,
        occurred_at=utcnow(),
        created_by=user_id,
    )
    db.add(entry)
    db.flush()
    return entry


def history_for_part(
    db: Session, *, workspace_id: UUID, part_id: UUID, limit: int = 200
) -> list[StockEntry]:
    return list(
        db.execute(
            select(StockEntry)
            .where(StockEntry.workspace_id == workspace_id)
            .where(StockEntry.part_id == part_id)
            .order_by(StockEntry.occurred_at.desc())
            .limit(limit)
        ).scalars()
    )


def history_for_lot(
    db: Session, *, workspace_id: UUID, lot_id: UUID, limit: int = 200
) -> list[StockEntry]:
    return list(
        db.execute(
            select(StockEntry)
            .where(StockEntry.workspace_id == workspace_id)
            .where(StockEntry.lot_id == lot_id)
            .order_by(StockEntry.occurred_at.desc())
            .limit(limit)
        ).scalars()
    )


def history_for_storage(
    db: Session, *, workspace_id: UUID, storage_location_id: UUID, limit: int = 200
) -> list[StockEntry]:
    return list(
        db.execute(
            select(StockEntry)
            .where(StockEntry.workspace_id == workspace_id)
            .where(StockEntry.storage_location_id == storage_location_id)
            .order_by(StockEntry.occurred_at.desc())
            .limit(limit)
        ).scalars()
    )


def history_global(db: Session, *, workspace_id: UUID, limit: int = 500) -> list[StockEntry]:
    return list(
        db.execute(
            select(StockEntry)
            .where(StockEntry.workspace_id == workspace_id)
            .order_by(StockEntry.occurred_at.desc())
            .limit(limit)
        ).scalars()
    )
