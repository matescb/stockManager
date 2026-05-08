# Orders API

Audience: engineer

Purchase orders, line entries, and the receive workflow that emits ledger rows + lots and advances the order status machine.

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. Mounted at `/api/orders` (`backend/app/main.py:374`). Live-part guard refuses bindings against archived parts (`orders.py:30-38`).

## Status machine

`_order_status` in `backend/app/domain/orders/service.py:28-37` computes the post-receive state from the entries:

| State | Trigger |
|---|---|
| `draft` | order created with no entries (`orders.py:136`). |
| `open` | order has entries, sum of `quantity_received` is 0. |
| `partial` | `0 < sum(received) < sum(ordered)`. |
| `received` | `sum(received) == sum(ordered)`. Sets `received_on` to today if unset (`service.py:171-174`). |
| `cancelled` | TODO(verify): set via `PATCH /api/orders/{order_id}` `status` field. Receive against a cancelled order returns `400 "order is cancelled"` (`service.py:50-51`). |

## Order CRUD

### `GET /api/orders`

List orders.

**Query**

| Field | Type | Notes |
|---|---|---|
| `archived` | bool | Default `false`. |
| `q` | string | ILIKE on `name`, `supplier`, `comments` (`orders.py:113-114`). |
| `order_status` | string | Exact filter on `status`. |
| `limit` | int | Default `200`, max `1000`. |

**Response** — `200 OK` — array of order objects, each carrying `totals: { ordered, received }` summed from entries (`orders.py:118-122`).

```json
{ "id": "…", "name": "…", "order_type": "…", "supplier": "…",
  "status": "open", "ordered_on": "…" | null, "expected_on": "…" | null, "received_on": "…" | null,
  "currency": "USD", "comments": "…",
  "archived_at": "…" | null,
  "totals": { "ordered": 100, "received": 75 },
  "created_at": "…", "updated_at": "…" }
```

**Notes**

- Sorted `updated_at DESC`.
- Source: `backend/app/api/routes/orders.py:101-122`.

### `POST /api/orders`

Create an order with optional initial entries.

**Request** — `OrderCreateIn`: `name`, `order_type`, `supplier`, `ordered_on?`, `expected_on?`, `currency`, `comments?`, `entries: OrderEntryIn[]`. Each entry needs `part_id` (live), `name?`, `quantity_ordered`, `unit_price?`, `currency?`, `comments?`. TODO(verify): exhaustive optionality.

**Response** — `201 Created` — serialised order with totals.

**Errors** — `404 part.not_found` for any entry whose `part_id` is missing or archived (`orders.py:143`).

**Notes**

- Initial status: `draft` if no entries, otherwise `open` (`orders.py:136`).
- `order_index` set to enumeration index (`orders.py:142-158`).
- Source: `backend/app/api/routes/orders.py:125-161`.

### `GET /api/orders/{order_id}`

Fetch order + entries.

**Response** — `200 OK`

```json
{ "data": { "order": <Order>, "entries": [ <Entry>, … ] }, "status": { … } }
```

**Errors** — `404 order not found` (`orders.py:76-80`).

**Notes**

- Source: `backend/app/api/routes/orders.py:164-171`.

### `PATCH /api/orders/{order_id}`

Update editable fields.

**Request** — `OrderPatchIn` (partial). TODO(verify): full editable field list (status transitions, dates).

**Notes**

- Source: `backend/app/api/routes/orders.py:174-181`.

### `POST /api/orders/{order_id}/archive`

Soft-archive (admin gate via `require_resource_access`). Logs polymorphic counts.

**Notes**

- Source: `backend/app/api/routes/orders.py:187-219`.

### `POST /api/orders/{order_id}/restore`

Clear `archived_at`. Same admin gate.

**Notes**

- Source: `backend/app/api/routes/orders.py:222-228`.

## Order entries

### `POST /api/orders/{order_id}/entries`

Append an entry. Auto-flips `status="draft"` to `"open"` (`orders.py:258-259`).

**Request** — `OrderEntryIn`. TODO(verify): exact field list.

**Response** — `201 Created` — serialised entry.

**Errors** — `404 part.not_found` if `part_id` is missing or archived.

**Notes**

- `order_index` is `max(order_index)+1` (`orders.py:235-243`).
- The part Authorized-supply tab can create entries through this route; see [parts frontend](../frontend/parts.md) for the UI flow.
- Source: `backend/app/api/routes/orders.py:231-261`.

### `PATCH /api/orders/{order_id}/entries/{entry_id}`

Update an entry.

**Request** — `OrderEntryPatch` (partial).

**Errors**

- `404` — missing entry or part.
- `400` — `quantity_ordered < quantity_received` would create negative outstanding (`orders.py:271-276`).

**Notes**

- Source: `backend/app/api/routes/orders.py:264-280`.

### `DELETE /api/orders/{order_id}/entries/{entry_id}`

Hard-delete the entry.

**Errors** — `400` if `quantity_received > 0` (`orders.py:287-288`).

**Notes**

- Source: `backend/app/api/routes/orders.py:283-290`.

## Receive workflow

### `POST /api/orders/{order_id}/receive`

Apply a partial or full receive. All-or-nothing within the request — any per-line error rolls the whole call back via the dep (`orders.py:299-301`).

**Request** — `ReceiveIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `received_on` | date | no | Defaults to today's date when the receive completes the order (`service.py:171-174`). |
| `lines` | `ReceiveLine[]` | yes | One row per (entry, lot) split. |

`ReceiveLine` — `{ order_entry_id, quantity, storage_location_id?, lot_name?, serial_number? }`. TODO(verify): full schema.

**Behaviour per line** (`backend/app/domain/orders/service.py:90-167`):

1. Find the `OrderEntry`; reject if missing or unmatched (`part_id is None`).
2. Reject if `quantity > outstanding` (`outstanding = quantity_ordered - quantity_received`).
3. Validate part: must be in workspace.
4. If workspace `serial_tracking_enabled` AND part `serialized`: require `quantity == 1` and a non-blank `serial_number`.
5. Validate storage location (in workspace, not archived, not `is_full`).
6. Insert a new `Lot` with `source_type="purchase"`, `source_order_id=order.id`, `purchase_quantity=line.quantity`, `purchase_unit_cost=oe.unit_price`, `purchase_currency=oe.currency or order.currency`, `name=lot_name or "{order.name}#{oe.order_index+1}"`.
7. Insert a `StockEntry` with `operation_type="receive"`, `status="on_hand"`, `quantity_delta=line.quantity`, `order_id`, `order_entry_id`.
8. Increment `oe.quantity_received += line.quantity`.

After all lines, recompute `order.status` from the entries and set `received_on` if appropriate.

**Concurrency** — acquires the workspace's advisory stock-write lock (`lock_parts_for_stock_write`) BEFORE re-querying entries `FOR UPDATE` so a concurrent receive can't slip through the `outstanding` guard (BE2-001 / #247) (`service.py:53-81`).

**Response** — `200 OK`

```json
{ "data": {
    "order_id": "…", "status": "partial",
    "lots": [ "…", "…" ],
    "stock_entries": [ "…", "…" ]
}, "status": { … } }
```

**Errors**

`400` (`OrderError` mapped):

- `"order is cancelled"` (`service.py:50-51`).
- `"order entry <id> not in this order"` (`service.py:91-93`).
- `"cannot receive an entry without a part — match it first"` (`service.py:94-95`).
- `"line over-receives entry <id> (outstanding N, want M)"` (`service.py:97-100`).
- `"part not in workspace"` (`service.py:103-104`).
- `"serialized part X must be received one unit per line"` (`service.py:106-110`).
- `"serialized part X requires a serial_number on the receive line"` (`service.py:111-114`).
- `"storage location not in workspace"` / `"storage location is archived"` / `"storage location is marked full"` (`service.py:119-124`).

`409 stock.conflict_error` (`StockConflictError` mapped, separate from the `OrderError` set above):

- destination storage violates `single_part_only` or `existing_parts_only` constraints; raised by `enforce_storage_constraints` (`backend/app/domain/stock/service.py:330`) before the ledger write (PR #299, issue #280). Body extras: `{ message, constraint, storage_location_id }` where `constraint` is `"single_part_only"` or `"existing_parts_only"`.

**Notes**

- Source: `backend/app/api/routes/orders.py:293-303`.
- Service: `backend/app/domain/orders/service.py:40-192`.

## Activity feed

### `GET /api/orders/{order_id}/activity`

Combined timeline of `stock_entries` tagged with this `order_id` plus synthetic `order_created` / `order_updated` items.

**Query**

| Field | Type | Notes |
|---|---|---|
| `limit` | int | `_DEFAULT_LIMIT`/`_MAX_LIMIT` from `_activity.py`. |
| `before_occurred_at` | ISO-8601 | Cursor; `422` on parse failure (`orders.py:319-323`). |
| `before_id` | UUID | Cursor tiebreak. |

**Response** — `200 OK` — built by `build_activity` (same shape as `/api/parts/{part_id}/activity`). Synthetic events only on the head page.

**Notes**

- Source: `backend/app/api/routes/orders.py:306-359`.

## TODOs

- TODO(verify): `OrderCreateIn`, `OrderPatchIn`, `OrderEntryIn`, `OrderEntryPatch`, `ReceiveIn`, `ReceiveLine` — exact fields and optionality (`domain/orders/schemas.py`).
- TODO(verify): how `cancelled` status is set (PATCH? dedicated endpoint?).
- TODO(verify): `order_type` allowed values.
