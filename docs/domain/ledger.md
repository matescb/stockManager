# Stock Ledger

Audience: engineer

`stock_entries` is an append-only ledger. Current stock for any (part, lot, storage) bucket is `SUM(quantity_delta)` over filtered rows; there is no `qty` column anywhere. This page documents the operation-type vocabulary, the advisory-lock protocol, and the read APIs.

For the architectural rationale see [ADR-0001](../adr/0001-append-only-stock-ledger.md). For the model definition see [`data-model.md`](data-model.md#stock--lots).

## Append-only contract

Every mutation is one `INSERT` (or two for `move`). Nothing in the codebase issues `UPDATE` or `DELETE` against `stock_entries` outside the cross-table `SET NULL` paths driven by FK delete behaviour.

The ledger row schema (`backend/app/domain/stock/models.py:22-87`):

| Column | Notes |
|---|---|
| `quantity_delta` | `Numeric(18,6)` since alembic 0074. Negative for consume/release/move-out. The API still validates and returns whole numbers only — see [integer-only quantities](../ARCHITECTURE.md#integer-only-quantities-db-005--migration-0032). |
| `unit` | Immutable unit stamp copied from the part at write time by `stock/service.py::unit_for_part` (`'pcs'` today). Stamped per row rather than resolved through `parts.unit_of_measure` on read, so editing a part can never retroactively reinterpret history. See [unit stamping](#unit-stamping) below. |
| `status` | `on_hand` or `reserved`. The two statuses sum independently — a positive on-hand row never offsets a reserved row. |
| `operation_type` | The verb. See vocabulary below. |
| `lot_id`, `storage_location_id` | Bucket dimensions. NULL is a distinct bucket, not a wildcard — the `0013` trigger uses `IS NOT DISTINCT FROM` on these. |
| `related_entry_id` | Self-FK. Used by `move_in`/`move_out` to point at each other and by `release` to point at the `reserve` row it cancels. |
| `order_id`, `order_entry_id`, `project_id`, `build_id` | Origin tags. All `SET NULL` on parent delete so the audit trail survives a hard-delete of the parent. |
| `bag_signature` | SHA-256 hex of the normalised bag code on rows produced by scan-import. See [scan-import](scan-import.md). |
| `unit_price`, `currency` | Per-unit cost captured at write time (e.g. from an order line). Read-only after insert. |
| `comments` | Free-form text. Surfaced in history views. |

## `operation_type` values

The full vocabulary, where it's emitted, and what it means for the bucket sum.

| Value | Emitted by | Sign of `quantity_delta` | Notes |
|---|---|---|---|
| `add` | `stock/service.py::add_stock` (`backend/app/domain/stock/service.py:481`) | + | Manual stock-in. Optionally creates a Lot. |
| `remove` | `stock/service.py::remove_stock` (`backend/app/domain/stock/service.py:533`) | − | Manual consume. Validates `payload.quantity <= bucket sum`. |
| `move_out` | `stock/service.py::_write_move_pair`, via `move_stock` / `move_quantity` | − | Source side of a move. |
| `move_in` | `stock/service.py::_write_move_pair`, via `move_stock` / `move_quantity` | + | Destination side. `related_entry_id` ↔ matching `move_out`. |
| `adjust` | `stock/service.py::adjust_stock` (`backend/app/domain/stock/service.py:757`) | + or − | Cycle-count correction; writes `actual_quantity − current_bucket_sum` (`backend/app/domain/stock/service.py:747`). Skips writing when delta is 0. |
| `receive` | `orders/service.py::receive` (`backend/app/domain/orders/service.py:156`) | + | Order-receive. Always creates a new Lot. See [orders-and-receive](orders-and-receive.md). |
| `reserve` | `builds/service.py::apply_reservations` (`backend/app/domain/builds/service.py:186`) | + | `status='reserved'`. Ties up stock for a planned build. |
| `release` | `builds/service.py::release_reservations` (`backend/app/domain/builds/service.py:249`) | − | `status='reserved'`. `related_entry_id` ↔ the `reserve` row it cancels. |
| `build_consume` | `builds/service.py::consume` (`backend/app/domain/builds/service.py:403`) | − | BOM consume during a build. |
| `build_produce` | `builds/service.py::consume` (`backend/app/domain/builds/service.py:465`) | + | Sub-assembly output, when `project.associated_subassembly_part_id` is set. Always creates an output Lot. |

There is no enum on the column — the values are strings written by the services above. New values must be added at the call site **and** documented here.

Kitting (Track B3) writes **no new verb**: a kit is a move, so it emits ordinary `move_out` / `move_in` pairs. The only thing that marks them as a kit is `build_id` (and `build_stage_id` for a per-stage kit), which is otherwise NULL on a move — that is what puts a kit on `GET /api/builds/{id}/activity` beside the build's own consume rows. See [builds-and-bom](builds-and-bom.md#kitting).

## Service entry points

| Operation | Entry point | Notes |
|---|---|---|
| Compute current quantity (single bucket) | `domain/stock/service.py::current_quantity` | Two interpretations of `None`. See bucket semantics below. |
| Bulk per-part roll-up | `domain/stock/service.py::bulk_current_quantities` | One SQL query for many parts; reports use this. `backend/app/domain/stock/service.py:193-226` |
| Bulk per-lot roll-up | `domain/stock/service.py::bulk_current_quantities_by_lot` | `backend/app/domain/stock/service.py:229-259` |
| Per-part (storage, lot) breakdown | `domain/stock/service.py::stock_summary_for_part` | Used by the part detail page. |
| Per-storage breakdown | `domain/stock/service.py::stock_for_storage` | Used by storage-history and the `single_part_only` check. |
| Reserved sum for a part | `domain/stock/service.py::reserved_quantity` | `backend/app/domain/stock/service.py:288` |
| Available = on_hand − reserved | `domain/stock/service.py::available_quantity` | `backend/app/domain/stock/service.py:301` |
| Add stock | `domain/stock/service.py::add_stock` | Producer-side advisory lock, optional Lot creation, default-storage and serial-tracking enforcement. |
| Remove stock | `domain/stock/service.py::remove_stock` | Validates source FKs against workspace before the availability check. |
| Move stock | `domain/stock/service.py::move_stock` | Two rows under savepoint (circular FK on `related_entry_id`); optional `split_lot` creates a child Lot. |
| Adjust stock | `domain/stock/service.py::adjust_stock` | Writes `actual − current`; returns `None` when delta is zero. |
| History (per part / lot / storage / global) | `domain/stock/service.py::history_for_*` | Workspace-filtered, ordered by `occurred_at DESC`. |

## Reading current quantity

`current_quantity(db, *, workspace_id, part_id, storage_location_id=None, lot_id=None, status='on_hand', bucket_match=False)` (`backend/app/domain/stock/service.py:140-190`).

Two `None` semantics — picking the wrong one is a recurring footgun:

- `bucket_match=False` (default; report-style): `None` means *don't filter on this dimension*. Aggregates across all values for the part.
- `bucket_match=True` (mutate-side validators): `None` means *match the SQL NULL bucket specifically*, using `IS NULL`. Aligns with the 0013 trigger's `IS NOT DISTINCT FROM` semantics so the validator and the trigger never disagree.

The full rationale (BE-002 / DB-002 in v2 teardown) is in the docstring at `backend/app/domain/stock/service.py:150-169`. Validators in `remove_stock`, `move_stock`, `adjust_stock`, and `consume` all pass `bucket_match=True`.

**The return type is `Decimal`**, as it is for `bulk_current_quantities`, `bulk_current_quantities_by_lot`, `total_for_part`, `reserved_quantity`, `available_quantity`, `stock_summary_for_part` and `stock_for_storage`. `SUM()` over `NUMERIC` is exact and order-independent — the property that lets a balance be re-summed from scratch on every read without drifting. Don't coerce it: `float()` loses exactness and `int()` truncates. Round explicitly, with `math.ceil`, only where a whole number is genuinely the answer (a build count, a distributor package count), and serialise through `domain/_quantity.py::quantity_out`. `backend/scripts/check_quantity_coercions.py` enforces this in CI; see [ARCHITECTURE.md](../ARCHITECTURE.md#integer-only-quantities-db-005--migration-0032).

## Advisory locking

Two append-only writers racing on the same (workspace, part) bucket can both pass `qty <= current_quantity` before either inserts. The fix is `pg_advisory_xact_lock`, hashed on `(workspace_id, part_id)` (`backend/app/domain/stock/service.py:50-84`). Locks release at COMMIT/ROLLBACK; nested calls are re-entrant within the same transaction.

Helpers:

- `_lock_for_stock_write(db, *, workspace_id, part_id)` — single-part lock, called by every mutating ledger entry (producer **and** consumer; see the docstring for why the producer side isn't optional).
- `lock_parts_for_stock_write(db, *, workspace_id, part_ids)` — multi-part lock taken in deterministic UUID-string order to prevent AB/BA deadlocks. Used by `builds.consume`, `builds.apply_reservations`, `builds.release_reservations`, and `orders.receive` (`backend/app/domain/stock/service.py:87-102`).
- `_lock_for_storage_constraint(db, *, workspace_id, storage_location_id)` — second lock for the `single_part_only`/`existing_parts_only` cross-part race on a destination location. Stock writers acquire it after the per-part lock to keep the lock-ordering invariant; storage PATCH rechecks acquire only this lock before enabling flags (`backend/app/domain/stock/service.py:105-137`).

The DB-side fall-back is the `0013` trigger (`backend/alembic/versions/0013_stock_nonneg_trigger.py`). It re-aggregates the bucket on every INSERT and raises `check_violation` if the cumulative sum is negative. The trigger is what catches a raw SQL write (or a future bug) that bypasses the advisory lock — it is intentionally not the primary defence because it converts every concurrent-write race into a 500 instead of a clean 4xx.

## Move semantics — circular FK

`_write_move_pair` writes two rows where each row's `related_entry_id` references the other. PostgreSQL enforces FKs at INSERT (constraints aren't `DEFERRABLE`) so a single `add_all` flush always violates one direction. The implementation:

1. Open a `db.begin_nested()` savepoint.
2. INSERT `move_out` with `related_entry_id=None`.
3. INSERT `move_in` with `related_entry_id=out_id` (the pre-allocated UUID for the out row).
4. UPDATE `move_out.related_entry_id = in_id`.
5. Release the savepoint.

The savepoint isolates the partial state so an outside transaction never observes a dangling back-pointer (BE2-007).

When `split_lot=True` and a `source_lot_id` is given, the same savepoint also creates a child `Lot` (`source_type='split'`, `parent_lot_id=src_lot.id`); the destination row points at the new lot id.

Two callers share that pair-writer:

| Entry point | Quantity type | Used by |
|---|---|---|
| `move_stock(payload: MoveStockIn)` | `int` (pydantic) | `POST /api/stock/move`, `POST /api/lots/{id}/move`, the MCP move tool. |
| `move_quantity(..., quantity: Decimal, build_id=…, build_stage_id=…)` | `Decimal` | Kitting. In-process only — routing a kit through `MoveStockIn` would push every quantity through an `int` field, the truncating coercion alembic 0074 widened the columns to avoid. No `split_lot`: a kit relocates a bag, it does not subdivide a lot. |

Both call `_prepare_move` first, so the per-part advisory lock, the workspace checks on both ends, the bucket-matched availability check and `enforce_storage_constraints` on the destination have one implementation.

## Unit stamping

Every ledger row records the unit its quantity is measured in, copied from the part **at write time** by `stock/service.py::unit_for_part` and never resolved through `parts.unit_of_measure` on read. Storing the unit only on the part would make an edit rewrite the past: flip a part from `pcs` to `m` and 500 pieces silently become 500 metres, with no audit trail and no way to tell which reading was intended. A ledger exists so a written row stays a permanent, self-describing fact, so the unit is part of the row.

Every part is `'pcs'` today, so the stamp writes exactly what the column's server default already produced — nothing observable has changed yet. The stamp is applied by all eleven allow-listed `StockEntry(...)` constructions (add / remove / move / adjust / receive / reserve / release / build consume / build produce). A `move` pair and a `release` counter-row inherit the unit of the row they mirror, or the two would not cancel.

Three triggers from alembic 0077 back that up. They are **backstops, not the control** — the same posture as the workspace FK triggers (see [ADR-0028](../adr/0028-hard-delete-policy-and-workspace-trigger-contract.md)) — and only ever fire for raw SQL that went around the service. All three raise SQLSTATE `23514`.

| Trigger | Fires on | Rejects |
|---|---|---|
| `stock_entries_unit_match_check` | `BEFORE INSERT` | A row whose `unit` disagrees with its part's `unit_of_measure`. Passes a NULL `part_id` (a hard-deleted part leaves its history behind, and the stamp is all that remains of what it measured), and passes silently when the part is not in the row's workspace so it cannot become a cross-workspace existence oracle — `stock_entries_workspace_fk_check` owns that error. |
| `stock_entries_unit_immutable_check` | `BEFORE UPDATE OF unit` | Any change to an existing row's `unit`. The ledger is append-only. |
| `parts_unit_of_measure_change_check` | `BEFORE UPDATE OF unit_of_measure` | Changing a part's unit once it has **any** ledger row. |

**Changing a part's unit is allowed only while the part has no ledger rows at all** — not merely while its balance is zero. Zeroing stock does not remove rows; nothing does. Under a net-zero rule a part's history would hold `pcs` rows and `m` rows together, and every roll-up built on `current_quantity` would go on summing them. "No rows at all" is the only rule that keeps a part's ledger single-unit by construction, which is what `SUM(quantity_delta)` silently depends on. The supported path for a part with history is to zero the stock out and re-add it, or to create a new part — both of which leave a correct trail.

## Reservations

`apply_reservations` writes one `status='reserved'`, `operation_type='reserve'` row per consumable BOM entry, sized by `_required(entry, part, build_qty)` (which folds in `attrition_percentage` and `attrition_min_quantity`). It does *not* bind a lot or storage location — those are picked at consume time (`backend/app/domain/builds/service.py:147-196`).

`release_reservations` finds every `reserve` row tied to the build that has no matching `release` (`status='reserved'`, `operation_type='release'`, `related_entry_id == reserve.id`) and writes a counter row with `quantity_delta=-r.quantity_delta`. Idempotent — calling twice writes once. (`backend/app/domain/builds/service.py:199-260`.)

`available_quantity = on_hand − reserved` is the universal "can I consume from this part?" formula (`backend/app/domain/stock/service.py:301-306`).

## Things to never do

- **Never compute current stock outside `current_quantity` / `bulk_current_quantities`.** The ad-hoc `SELECT … SUM(quantity_delta) GROUP BY part_id` blocks in `reports.py` were funnelled through `bulk_current_quantities` precisely so the invariant has exactly one expression in code (BE2-005).
- **Never `UPDATE` or `DELETE` `stock_entries` from a service.** Cross-table parent deletes drive `SET NULL` on the FK columns; nothing else touches existing rows.
- **Never skip the per-part advisory lock on a producer-side write.** "Positive deltas can't go negative" is true in isolation but ignores invariant-read races (`single_part_only`, default-storage-mandatory) and the 0013 trigger turning a controllable 4xx into a 500. See the docstring at `backend/app/domain/stock/service.py:65-77`.
- **Never assume `lot_id IS NULL` and `lot_id IS NOT NULL` aggregate together.** The trigger and the validators treat NULL as a distinct bucket. Use `bucket_match=True` for any read whose result drives a write.
- **Never put a ledger quantity through `float()` or `int()`.** The columns are `Numeric(18,6)`; both coercions destroy a measured value silently, and today nothing would notice because every value is still whole. `backend/scripts/check_quantity_coercions.py` fails the build on either; annotate a deliberate exception with `# noqa: quantity-decimal` and say why.
