# Storage API

Audience: engineer

Storage location CRUD, archive/restore, plus per-location stock readout and history.

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

## TODOs

- TODO(verify): `StorageIn` / `StoragePatch` field optionality (defined in `domain/storage/schemas.py`).
