# Lots and Serials

Audience: engineer

A lot is a tracked batch of one part. Lots are how the system represents date-coded stock, purchase batches, build outputs, splits, and individual serialised units. This page covers the lifecycle, the split mechanism via `parent_lot_id`, and serial-tracking enforcement.

For the `Lot` model see [`data-model.md`](data-model.md#stock--lots).

## Lifecycle by `source_type`

`Lot.source_type: String(20)` (`backend/app/domain/lots/models.py:31`). Five values, each created by exactly one code path:

| `source_type` | Created by | Notes |
|---|---|---|
| `manual` | `stock/service.py::add_stock` (`backend/app/domain/stock/service.py:455-470`) | When the operator types in stock-add with lot details. |
| `purchase` | `orders/service.py::receive` (`backend/app/domain/orders/service.py:130-145`) | Sets `source_order_id`, `purchase_quantity`, `purchase_unit_cost`, `purchase_currency`. One Lot per receive line. Lot name defaults to `"{order.name}#{order_index+1}"`. |
| `split` | `stock/service.py::move_stock` (`backend/app/domain/stock/service.py:613-630`) | Created when `move_stock(... split_lot=True, source_lot_id=...)`. Parent lot is preserved; the child carries `parent_lot_id=src_lot.id`, name `"{src_name}-split"`, copies expiration/cost/currency. |
| `build` | `builds/service.py::consume` (`backend/app/domain/builds/service.py:445-454`) | Output lot for a sub-assembly produced by a build. Sets `source_build_id`. |
| `bag` | `api/routes/parts_scan.py` (the bulk-import flow) | TODO(verify): scan-import creates lots via `add_stock` with `source_type='manual'` rather than a dedicated `'bag'` value — confirm whether any code path actually writes `'bag'` or whether the value listed in some schemas is dead. |

Cross-domain FKs are pinned by name (alembic 0018) so downgrade can drop them by name (`backend/app/domain/lots/models.py:32-44`):

- `fk_lots_source_order_id` → `orders.id`, `SET NULL`.
- `fk_lots_source_build_id` → `builds.id`, `SET NULL`.

## Split mechanism

Splits happen inside `move_stock` when the operator wants to move *part* of a reel/batch to a new location while retaining the rest as a distinct trackable unit (`backend/app/domain/stock/service.py:602-668`).

The full sequence under one savepoint (`db.begin_nested()`):

1. Look up source lot, validate workspace ownership.
2. Create child `Lot` with `source_type='split'`, `parent_lot_id=src_lot.id`, copying `description`, `expiration_date`, `purchase_unit_cost`, `purchase_currency`. Comment: `"split from {src_lot.id}"`.
3. Write `move_out` row referencing **source** lot/storage, `quantity_delta = -payload.quantity`.
4. Write `move_in` row referencing **child** lot and destination storage, `quantity_delta = +payload.quantity`.
5. Patch `move_out.related_entry_id = in_id` (closes the circular FK).

The savepoint contains the partial state so a downstream raise (e.g. the `0013` trigger noticing inconsistency on the IN row) cleans up the dangling lot rather than orphaning it under `src_lot.id`.

`parent_lot_id` is `SET NULL` on parent delete, so a hard-deleted parent leaves the child as a top-level lot rather than vanishing. (Hard-delete of lots isn't exposed by routes; archive is the documented path.)

## Lot fields

`Lot` (`backend/app/domain/lots/models.py:10-47`):

| Column | Notes |
|---|---|
| `part_id` | FK, CASCADE. A lot is always for exactly one part. |
| `name` | Free-form. Defaults vary by `source_type` (e.g. `"{order_name}#N"` for purchase). |
| `serial_number` | Indexed. Required for serialised stock — see below. |
| `parent_lot_id` | Self-FK, `SET NULL`. Set on `source_type='split'`. |
| `description`, `comments` | Free text. |
| `expiration_date` | Date; surfaced in the expiring-lots report. |
| `source_type` | Lifecycle marker; see table above. |
| `source_order_id`, `source_build_id` | Origin pointers, `SET NULL`. |
| `purchase_quantity`, `purchase_unit_cost`, `purchase_currency` | Captured at creation; the per-unit cost is what flows into `stock_entries.unit_price` for downstream value reports. |

`workspace_id` and `archived_at` are inherited from `WorkspaceOwned`. There is no DB CHECK enforcing a single positive quantity per lot — the ledger sums per-(lot, storage) bucket.

## Serial tracking

Two boolean flags cooperate:

- `Workspace.serial_tracking_enabled` (`backend/app/domain/workspaces/models.py:32`) — workspace-wide opt-in. Default false.
- `Part.serialized` (`backend/app/domain/parts/models.py:76`) — per-part flag. Default false.

When both are true, every stock-add for that part is forced to a one-unit serialised lot. Enforced in two places:

- `stock/service.py::add_stock` (`backend/app/domain/stock/service.py:431-436`): `quantity == 1`, `lot.serial_number` non-empty.
- `orders/service.py::receive` (`backend/app/domain/orders/service.py:106-114`): same rule, per receive line.

Build-produce output (`backend/app/domain/builds/service.py:445-456`) does **not** automatically split into serialised units even when the sub-assembly part is serialised. TODO(verify): is this an intentional gap or a missed enforcement point?

The `lots.serial_number` column has its own index (`index=True` at `backend/app/domain/lots/models.py:26`) for fast serial lookup.

## Reads

| Operation | Entry point | Notes |
|---|---|---|
| Per-lot current quantity (bulk) | `domain/stock/service.py::bulk_current_quantities_by_lot` | `lot_ids=None` aggregates every lot in the workspace; used by stock-value and expiring-lots reports. |
| Per-lot history | `domain/stock/service.py::history_for_lot` | Workspace-filtered, `occurred_at DESC`, default limit 200. |
| Lot endpoints | `api/routes/lots.py` | TODO(verify): not read — confirm CRUD + archive surface. |

## Indexes

`Lot.__table_args__` (`backend/app/domain/lots/models.py:11-22`):

- `ix_lots_ws_part` — listing by part.
- `ix_lots_ws_archived` — universal active-row filter.
- `ix_lots_ws_name_trgm` — pg_trgm GIN on `name` for ILIKE search (alembic 0018).
- `serial_number` — single-column btree for fast serial lookup.

## Things to never do

- **Never overwrite `parent_lot_id` after creation.** Lineage is the only persistent record that two lots share an origin.
- **Never write a `move_in` referencing a `move_out`'s lot when `split_lot` is true.** The split flow points the IN row at the *child* lot id, not the source.
- **Never bypass the per-(workspace, part) advisory lock when creating a lot tied to a stock entry.** The lot row is flushed first, then the StockEntry — inserting the StockEntry under a different lock state than the validators read would re-introduce the BE CRIT-1 race.
