# Orders and Receive

Audience: engineer

`orders` + `order_entries` is the purchasing/sales surface. The interesting flow is `receive()` — the orchestration that turns a partial or full receipt into ledger writes plus lot creation. This page documents that flow; CRUD on orders themselves is conventional.

For models see [`data-model.md`](data-model.md#orders).

## Order shape

`Order` (`backend/app/domain/orders/models.py:20-42`):

| Column | Notes |
|---|---|
| `name` | Free-form. |
| `order_type` | `purchase | sales`. Default `purchase`. The receive flow treats both the same — only emitting language differs (TODO(verify): trace whether `sales` orders ever route through `receive`). |
| `supplier` | Free text; not an FK. |
| `status` | `draft | open | partial | received | cancelled`. Recomputed by `_order_status` on every receive. |
| `ordered_on`, `expected_on`, `received_on` | Dates. `received_on` is auto-set when `status` first reaches `received`. |
| `currency` | ISO 4217 default for entries that omit it. |

`OrderEntry` (`backend/app/domain/orders/models.py:45-63`):

| Column | Notes |
|---|---|
| `order_id` | FK, **CASCADE**. |
| `part_id` | FK, `SET NULL`. **Nullable** — entries can pre-exist their part match (e.g. a freshly-imported PO line). `name` is the free-text fallback in that case. |
| `quantity_ordered`, `quantity_received` | Both `>= 0` (CHECK constraints `ck_order_entries_qty_ordered_nonneg` / `ck_order_entries_qty_received_nonneg`, alembic 0032). |
| `unit_price`, `currency` | Per-line price; falls back to `Order.currency` when null. |
| `order_index` | Display-order integer. |

## Status recomputation

`_order_status(entries)` (`backend/app/domain/orders/service.py:28-37`):

```
no entries                     → "draft"
sum(received) == 0             → "open"
0 < sum(received) < sum(ordered) → "partial"
sum(received) == sum(ordered)  → "received"
```

`cancelled` is a terminal state set elsewhere and short-circuits the receive call (`backend/app/domain/orders/service.py:50-51`).

## Receive orchestration

`orders/service.py::receive` (`backend/app/domain/orders/service.py:40-192`). All-or-nothing within the request — the caller is responsible for the surrounding commit.

The order of operations is load-bearing:

1. **Refuse** if `order.status == 'cancelled'`.
2. **Acquire the advisory stock-write lock BEFORE reading `OrderEntry` rows** (`backend/app/domain/orders/service.py:53-71`). Reading first and locking second is a TOCTOU race — a concurrent receive can slip past the `outstanding` guard while both threads hold stale `quantity_received`. Fix:
   - Run a lightweight preliminary query collecting `(id, part_id)` for every order entry.
   - Call `lock_parts_for_stock_write` on the deduplicated, sorted `part_id` set.
   - Re-query with `with_for_update()` so `quantity_received` reflects any in-flight writes that committed before the lock was taken.
3. **Per receive line** (`backend/app/domain/orders/service.py:90-167`):
   - Validate the `order_entry_id` belongs to this order.
   - Reject if the entry has no `part_id` ("match it first").
   - Compute `outstanding = quantity_ordered - quantity_received`. Reject if `line.quantity > outstanding` ("over-receive").
   - Validate `part.workspace_id == ws.id` — defence-in-depth.
   - If serial-tracking is on AND the part is `serialized`: enforce `line.quantity == 1` and a non-empty `serial_number`.
   - Validate the optional `storage_location_id`: workspace, not archived, not `is_full`.
   - Create a `Lot` with `source_type='purchase'`, `source_order_id=order.id`. Lot name defaults to `f"{order.name}#{oe.order_index + 1}"` if not supplied. `purchase_quantity`, `purchase_unit_cost`, `purchase_currency` come from the order entry / order.
   - Write a `StockEntry` with `operation_type='receive'`, `lot_id=lot.id`, `order_id`, `order_entry_id`, `quantity_delta=line.quantity`, `unit_price`, `currency`.
   - Bump `oe.quantity_received += line.quantity`.
4. **Recompute order status** via `_order_status(list(entries_by_id.values()))`. Set `received_on` from the payload, or auto-set to today's date if the status transitioned to `received` and no date was supplied.
5. **Return** a dict of `{ order_id, status, lots: [...], stock_entries: [...] }`.

A `log.info("order received", ...)` line emits structured fields for ops dashboards (`backend/app/domain/orders/service.py:177-186`).

## Service entry point

| Operation | Entry point | Notes |
|---|---|---|
| Receive (partial or full) | `domain/orders/service.py::receive` | All-or-nothing; takes per-part advisory lock first; emits one Lot + one StockEntry per line. |
| Status helper | `domain/orders/service.py::_order_status` | Pure function on a list of `OrderEntry`. |

## What gets written per line

For each receive line:

| Side effect | Source |
|---|---|
| One new `Lot` row | `backend/app/domain/orders/service.py:130-144` |
| One new `StockEntry` row | `backend/app/domain/orders/service.py:147-164` |
| `OrderEntry.quantity_received` bumped | `backend/app/domain/orders/service.py:166` |
| `OrderEntry.updated_by` set | `backend/app/domain/orders/service.py:167` |

After all lines:

| Side effect | Source |
|---|---|
| `Order.status` recomputed | `backend/app/domain/orders/service.py:170` |
| `Order.received_on` set if newly `received` | `backend/app/domain/orders/service.py:171-174` |
| `Order.updated_by` set | `backend/app/domain/orders/service.py:175` |

## Order ↔ stock cross-references

The `stock_entries.order_id` and `stock_entries.order_entry_id` columns (added in alembic 0018, `SET NULL`) are the audit trail. After a hard-delete of the order or the order entry — not exposed by routes today — the stock rows survive with NULL pointers; the lot still carries `source_order_id` until that's also nulled.

`Lot.source_order_id` (`fk_lots_source_order_id`, `SET NULL`) plays the same role for the lot side.

## Receive errors

`OrderError` is raised for application-level violations and is mapped to a 4xx in the route layer (`backend/app/api/routes/orders.py` — TODO(verify): list specific HTTP codes).

Common cases:

- `"order is cancelled"` — caller tried to receive against a cancelled order.
- `"order entry {id} not in this order"` — payload referenced the wrong order's entry.
- `"cannot receive an entry without a part — match it first"` — the order entry has `part_id IS NULL` (free-text line).
- `"line over-receives entry {id} (outstanding {N}, want {M})"` — total would exceed `quantity_ordered`.
- `"serialized part {name} must be received one unit per line"` / `"… requires a serial_number on the receive line"` — serial-tracking enforcement.
- `"storage location not in workspace"` / `"… is archived"` / `"… is marked full"`.

Each raise rolls back the entire receive call (transaction-level). There is no per-line partial-success.

## Things to never do

- **Never read `OrderEntry.quantity_received` before acquiring the advisory lock.** That's BE2-001 / #247 — see step 2 above.
- **Never compute `Order.status` outside `_order_status`.** It's the single source of truth for the state machine.
- **Never write a `receive` ledger row without also creating its lot.** Receive always produces a lot per line; consumers downstream (cost-of-goods reports, provenance) assume the linkage exists.
