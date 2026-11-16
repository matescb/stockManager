# Storage API

Audience: engineer

Storage location CRUD, archive/restore, per-location stock readout and history, plus box intake and box audit ([phase 15](../phases/15-box-intake-and-audit.md)).

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. Mounted at `/api/storage` (`backend/app/main.py:370`).

## Routes

### `GET /api/storage`

List storage locations.

**Query**

| Field | Type | Notes |
|---|---|---|
| `archived` | bool | Default `false`; toggles `archived_at IS NULL` (`storage.py:46-48`). |
| `q` | string | ILIKE on `name`, `description` (`storage.py:49-51`). |
| `limit` | int | Default `200`, max `1000`. |

**Response** — `200 OK` — array of:

```json
{ "id": "…", "name": "…", "description": "…",
  "single_part_only": bool, "existing_parts_only": bool, "is_full": bool,
  "archived_at": "…" | null }
```

**Notes**

- Sorted by `name`.
- Source: `backend/app/api/routes/storage.py:37-53`.

### `POST /api/storage`

Create a storage location.

**Request** — `StorageIn`: `name`, `description?`, `single_part_only`, `existing_parts_only`, `is_full`. TODO(verify): exact required/optional split.

**Response** — `201 Created` — serialised storage row.

**Notes**

- Source: `backend/app/api/routes/storage.py:56-70`.

### `GET /api/storage/{storage_id}`

Fetch a single location.

**Errors** — `404 storage.not_found` (`storage.py:73-77`).

**Notes**

- Source: `backend/app/api/routes/storage.py:80-82`.

### `PATCH /api/storage/{storage_id}`

Update editable fields.

**Request** — `StoragePatch` (partial).

**Errors** — `404 storage.not_found`.

**Notes**

- Source: `backend/app/api/routes/storage.py:85-91`.

### `POST /api/storage/{storage_id}/archive`

Soft-archive (`archived_at = now`).

**Errors**

- `404 storage.not_found` — uses `require_resource_access` so non-admins probing a foreign workspace's id get `404` not `403` (BE2-009) (`storage.py:97-101`).
- `409 storage.has_stock` — location still holds positive on-hand stock. Body includes `blocking: [{ part_id, lot_id, quantity }, …]` (`storage.py:107-123`).

**Notes**

- Admin-gated via `require_resource_access(role="admin")` (`storage.py:99-101`).
- Source: `backend/app/api/routes/storage.py:97-125`.

### `POST /api/storage/{storage_id}/restore`

Clear `archived_at`. Same admin gate.

**Notes**

- Source: `backend/app/api/routes/storage.py:128-134`.

### `GET /api/storage/{storage_id}/parts`

Per-(part, lot) on-hand for the location.

**Response** — `200 OK`

```json
{ "data": [ { "part_id": "…", "lot_id": "…" | null, "quantity": 25 } ], "status": { … } }
```

**Notes**

- Service: `domain/stock/service.py::stock_for_storage`.
- Source: `backend/app/api/routes/storage.py:137-150`.

### `GET /api/storage/{storage_id}/history`

Ledger entries that touched this location. Two response shapes — bare list (default) or paged envelope when `?cursor=` / `?paged=true` is set, sorted `occurred_at DESC`.

**Query**

| Field | Type | Notes |
|---|---|---|
| `limit` | int | Default `200`, max `1000`. |
| `cursor` | string | HMAC-signed; tampering → 400. |
| `paged` | bool | Force the paged envelope. |

**Response — entry shape** — `{ id, part_id, lot_id, quantity_delta, operation_type, comments, occurred_at }` (`storage.py:153-162`).

**Errors** — `404 storage.not_found`.

**Notes**

- Source: `backend/app/api/routes/storage.py:165-197`.

### `POST /api/storage/{storage_id}/scan/resolve`

What a scanned bag or typed MPN refers to, relative to this box. Read-only. Rate limit `120/minute` per workspace.

**Body** — `{ mpn?, bag_signature?, raw_bag_code?, provider_lookup?: bool = true }`. When `raw_bag_code` is sent, the signature is recomputed and a mismatch is `422 stock.bag_signature_mismatch`.

**Response** — `200 OK`

```json
{ "status": "bag_here" | "bag_elsewhere" | "existing_part" | "provider_match" | "not_found",
  "part": { "id", "name", "mpn", "manufacturer", "description" } | null,
  "bag": { "lot_id", "lot_name", "here": bool, "quantity_here",
           "locations": [ { "storage_location_id", "name", "quantity" } ] } | null,
  "provider": { "mpn", "manufacturer", "description", "image_url" } | null }
```

**Notes**

- Bag state is found by signature: the newest entry carrying it → its lot → that lot's non-zero buckets. A bag with nothing left, or one recorded without a lot, resolves as `existing_part`.
- `provider_match` calls the workspace's primary provider (cached); `provider_lookup: false` skips it.
- Source: `backend/app/api/routes/storage_scan.py`, `domain/stock/box_scan.py::resolve_scan`.

### `POST /api/storage/{storage_id}/intake`

Put one bag into the box as its own lot. Rate limit `120/minute` per workspace.

**Body** — `BoxIntakeIn`: `{ part_id?, mpn?, quantity (>0), bag_signature?, raw_bag_code?, lot_name?, lot_serial?, comments?, on_existing: "move" | "add_new" = "move" }`.

**Response** — `200 OK` — `{ status: "added" | "moved" | "already_here", part, part_created, lot_id, quantity, moved_from: [ { storage_location_id, quantity } ] }`.

**Errors** — `404 storage.not_found`; `404 part.not_found` (foreign/archived `part_id`); `404 box_scan.part_not_found` (MPN not in the library and the provider has no match / no provider); `422 box_scan.no_part`; `422 stock.bag_signature_mismatch`; `400 storage.archived`; `409 stock.constraint_violation`; `400 stock.operation_error`.

**Notes**

- Part resolution order: `part_id` → the known bag's part → active part by exact MPN → create from the provider (`provider_import.create_from_provider_lookup`).
- A known bag with stock somewhere and `on_existing="move"` is moved here and its count set to `quantity`; re-sending the same bag is `already_here`.
- Audit log: `storage.intake.<status>` with the box, part and lot ids.
- Source: `domain/stock/box_scan.py::intake_bag`.

### `GET /api/storage/{storage_id}/bags`

The box's recorded contents, one row per non-empty (part, lot) bucket.

**Response** — `200 OK` — array of `{ part, lot: { id, name } | null, quantity, bag_signatures: [] }`, sorted by part name then lot name. `lot: null` is stock recorded without a lot.

**Errors** — `404 storage.not_found`.

### `POST /api/storage/{storage_id}/audit`

Apply an audit's verdicts in one transaction.

**Body** — `BoxAuditIn`: `{ lines: [ { part_id, lot_id: uuid | null, action: "count" | "write_off" | "move", actual_quantity?, to_storage_location_id? } ] (1–1000), comments? }`.

**Response** — `200 OK` — `{ count, write_off, move, unchanged }`.

**Errors** — `404 storage.not_found` (box or a move destination outside the workspace); `400 stock.operation_error` with `line` (0-based) — the whole audit is rolled back.

**Notes**

- `count` and `write_off` are `adjust_stock` on the exact (box, lot) bucket (write-off = 0); `move` moves the bucket's full quantity with `move_quantity`.
- Audit log: one `storage.audited` row with the counts.

## TODOs

- TODO(verify): `StorageIn` / `StoragePatch` field optionality (defined in `domain/storage/schemas.py`).
