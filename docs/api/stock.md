# Stock & Lots API

Audience: engineer

Stock movement (add / remove / move / adjust), the global history feed, and lot CRUD + per-lot history. Two routers: `/api/stock` (`backend/app/main.py:371`) and `/api/lots` (`backend/app/main.py:372`).

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. The append-only ledger is a hard invariant — see [ADR-0001](../adr/0001-append-only-stock-ledger.md). All quantity reads roll up via `domain/stock/service.py`; never derive `current_quantity` outside that module (CLAUDE.md).

`StockError` from the service layer maps to `400`; `StockConflictError` (over-allocation against a `default_storage_mandatory` slot) maps to `409` with `constraint`, `storage_location_id` (`stock.py:66-74`).

## Stock movement

### `POST /api/stock/add`

Append a positive ledger entry.

**Request** — `AddStockIn`. Notable fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `part_id` | UUID | yes | |
| `quantity` | int (>0) | yes | |
| `storage_location_id` | UUID | no | |
| `lot` | `LotInput` | no | Inline lot details — promoted into a `lots` row by the service. |
| `lot_id` | UUID | no | Reuse an existing lot. |
| `unit_price`, `currency` | numeric / str | no | |
| `comments` | string | no | |
| `bag_signature` | string | no | SHA-256 of the normalised bag code (CLAUDE.md "bag_signature"). |
| `raw_bag_code` | string | no | When supplied alongside `bag_signature`, the signature is recomputed and a mismatch becomes `422` (`stock.py:57-63`). |

TODO(verify): full schema field list and which combination of `lot` vs `lot_id` is accepted.

**Response** — `200 OK` — serialised `StockEntry` (`stock.py:27-40`):

```json
{ "data": { "id": "…", "part_id": "…", "lot_id": "…" | null, "storage_location_id": "…" | null, "quantity_delta": 25, "status": "on_hand", "unit_price": 0.42, "currency": "USD", "operation_type": "add", "comments": null, "occurred_at": "…" }, "status": { … } }
```

**Errors**

- `422` — `bag_signature` doesn't match the recomputed digest of `raw_bag_code` (`stock.py:60-63`).
- `409` — `StockConflictError`. Body includes `message`, `constraint`, `storage_location_id` (`stock.py:66-74`).
- `400` — any other `StockError` (e.g. workspace mismatch on lot/storage) (`stock.py:75-76`).

**Notes**

- Source: `backend/app/api/routes/stock.py:49-77`.
- Service: `backend/app/domain/stock/service.py::add_stock`.

### `POST /api/stock/remove`

Append a negative ledger entry.

**Request** — `RemoveStockIn`. Notable fields: `part_id`, `quantity`, `storage_location_id?`, `lot_id?`, `comments?`. TODO(verify): full field list.

**Response** — `200 OK` — serialised entry.

**Errors** — `400` — any `StockError` (over-quantity, missing combo, etc.) (`stock.py:86-87`).

**Notes**

- Source: `backend/app/api/routes/stock.py:80-88`.
- Service: `backend/app/domain/stock/service.py::remove_stock`.

### `POST /api/stock/move`

Append a paired (out, in) entry across storage locations / lots.

**Request** — `MoveStockIn`. TODO(verify): exact fields (`part_id`, `quantity`, `from_storage_location_id`, `to_storage_location_id`, `source_lot_id`, etc.).

**Response** — `200 OK`

```json
{ "data": { "out": <StockEntry>, "in": <StockEntry> }, "status": { … } }
```

**Errors**

- `409` — `StockConflictError` with `constraint`, `storage_location_id` (`stock.py:95-103`).
- `400` — any other `StockError` (`stock.py:104-105`).

**Notes**

- Source: `backend/app/api/routes/stock.py:91-106`.
- Service: `backend/app/domain/stock/service.py::move_stock`.

### `POST /api/stock/adjust`

Reconcile the on-hand quantity for a (part, storage, lot) tuple to `actual_quantity`. The service computes the delta and either inserts an adjustment entry or returns `None` when no change.

**Request** — `AdjustStockIn`: `part_id`, `storage_location_id?`, `lot_id?`, `actual_quantity`, `comments?`.

**Response** — `200 OK`. When the actual matches the current, returns `{ data: null, status: { ..., message: "no change" } }`; otherwise `data` is the serialised entry (`stock.py:115`).

**Errors** — `400` — `StockError` (`stock.py:113-114`).

**Notes**

- Source: `backend/app/api/routes/stock.py:109-115`.
- Service: `backend/app/domain/stock/service.py::adjust_stock`.

### `GET /api/stock/history`

Workspace-wide ledger feed.

**Query**

| Field | Type | Notes |
|---|---|---|
| `limit` | int | Default `200`, max `1000` (`stock.py:119`). |

**Response** — `200 OK` — array of serialised entries. No cursor here; per-lot history is paged separately.

**Notes**

- Source: `backend/app/api/routes/stock.py:118-121`.
- Service: `domain/stock/service.py::history_global`.

## Lots

### `GET /api/lots`

List lots in the workspace.

**Query**

| Field | Type | Notes |
|---|---|---|
| `limit` | int | Default `200`, max `1000` (`lots.py:51`). |

**Response** — `200 OK` — array of lot objects. Each row carries a `current_quantity` from `current_quantity(db, ws_id, part_id, lot_id)` (`lots.py:62-64`).

```json
{ "data": [ { "id": "…", "part_id": "…", "name": "…", "serial_number": "…",
              "parent_lot_id": "…" | null, "description": "…", "comments": "…",
              "expiration_date": "…" | null, "source_type": "…",
              "purchase_quantity": 100, "purchase_unit_cost": 0.42, "purchase_currency": "USD",
              "current_quantity": 75, "created_at": "…" } ], "status": { … } }
```

**Notes**

- Sorted `created_at DESC`.
- Source: `backend/app/api/routes/lots.py:47-65`.

### `GET /api/lots/{lot_id}`

Fetch a single lot.

**Errors** — `404 lot.not_found` (`lots.py:68-72`).

**Notes**

- Source: `backend/app/api/routes/lots.py:75-79`.

### `PATCH /api/lots/{lot_id}`

Update editable fields.

**Request** — `LotPatch` (partial). `expiration_date` is parsed via `date.fromisoformat`. TODO(verify): full editable field list.

**Errors**

- `404 lot.not_found` (`lots.py:84`).
- `400 lot.invalid_expiration_date` — `expiration_date` not parseable (`lots.py:88-91`).

**Notes**

- Source: `backend/app/api/routes/lots.py:82-96`.

### `POST /api/lots/{lot_id}/move`

Move stock from this lot to a different storage location. The endpoint copies `part_id` + `source_lot_id` from the lot row, so the request body's same fields are ignored (`lots.py:102`).

**Request** — `MoveStockIn` (same shape as `/api/stock/move`).

**Response** — `200 OK`

```json
{ "data": { "out": "<entry id>", "in": "<entry id>" }, "status": { … } }
```

**Errors** — `400 lot.move_stock_error` — any `StockError` (`lots.py:105-107`).

**Notes**

- Source: `backend/app/api/routes/lots.py:99-108`.

### `POST /api/lots/{lot_id}/adjust-count`

Adjust the on-hand quantity for a single (lot, storage) pair.

**Request** — `LotAdjustIn`: `actual_quantity`, `storage_location_id?`, `comments?`.

**Response** — `200 OK`

```json
{ "data": { "id": "…" | null, "delta": -3 }, "status": { … } }
```

**Errors** — `400 lot.adjust_stock_error` — any `StockError` (`lots.py:121-124`).

**Notes**

- Source: `backend/app/api/routes/lots.py:111-125`.

### `GET /api/lots/{lot_id}/history`

Per-lot ledger feed. Two response shapes:

- Default — bare list (`?limit=200` is what the FE sends).
- Cursor mode — `?paged=true` or `?cursor=<…>` returns `{ items: [...], next_cursor: string | null }`.

**Query**

| Field | Type | Notes |
|---|---|---|
| `limit` | int | Default `200`, max `1000` (`lots.py:144`). |
| `cursor` | string | HMAC-signed; tampering → 400. |
| `paged` | bool | Force the paged envelope. |

**Response — entry shape** — `{ id, quantity_delta, storage_location_id, operation_type, comments, occurred_at }` (`lots.py:128-136`).

**Errors** — `404 lot.not_found` (`lots.py:148`).

**Notes**

- Sorted `occurred_at DESC` (`asc=False` in `paginate`) (`lots.py:165`).
- Source: `backend/app/api/routes/lots.py:139-171`.

## TODOs

- TODO(verify): exhaustive field lists for `AddStockIn`, `RemoveStockIn`, `MoveStockIn`, `AdjustStockIn`, `LotPatch`, `LotAdjustIn`, `LotInput` (defined in `domain/stock/schemas.py` and `domain/lots/schemas.py`).
