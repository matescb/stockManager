# Parts API

Audience: engineer

Parts CRUD, provider asset serving, MPN lookup, and the scan-import pipeline. Combines four routers — `parts_core`, `parts_assets`, `parts_scan`, `parts_provider` — that all mount at `/api/parts` (`backend/app/main.py:367-392`).

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. MPN uniqueness is enforced by partial unique index `uq_parts_ws_mpn` and pre-checked here — see [ADR-0004](../adr/0004-mpn-uniqueness-per-workspace.md). Provider assets are content-addressed at `{UPLOAD_DIR}/parts/{ws_id}/{sha}.{ext}` (CLAUDE.md "Hard invariants").

## Part CRUD

### `GET /api/parts`

List parts. Two response shapes selected by query (not by route):

- Default — bare list of part objects. `?limit=N` is honoured here too (default `50`, max `200`); legacy callers that need every part must pass `?limit=200` explicitly (PR #298, issue #286).
- Cursor mode — when `?paged=true` or `?cursor=<…>` is set, returns `{ items: [...], next_cursor: string | null }`.

**Query**

| Field | Type | Notes |
|---|---|---|
| `q` | string | ILIKE match against `name`, `mpn`, `manufacturer`, `internal_part_number`, `description` (`parts_core.py:91-101`). |
| `archived` | bool | Default `false`. Toggles `archived_at IS NULL` vs `IS NOT NULL` (`parts_core.py:88`). |
| `mpn` | string | Exact match. |
| `limit` | int | Default `50`, max `200`. |
| `cursor` | string | HMAC-signed; tampering returns 400 from `decode_cursor` (`parts_core.py:85`). |
| `paged` | bool | Force the paged envelope without supplying a cursor. |

**Response — default** — `200 OK`

```json
{ "data": [ <PartOut>, … ], "status": { … } }
```

**Response — paged** — `200 OK`

```json
{ "data": { "items": [ <PartOut>, … ], "next_cursor": "…" | null }, "status": { … } }
```

`PartOut` is built by `serialize_part` (`backend/app/api/routes/_parts_shared.py:44-85`); includes `id`, `part_type`, `name`, `manufacturer`, `mpn`, `internal_part_number`, `description`, `footprint`, `notes_markdown`, `low_stock_report_quantity`, `attrition_percentage`, `attrition_min_quantity`, `default_storage_location_id`, `default_storage_mandatory`, `serialized`, `published`, `linked_provider`, `linked_external_id`, `last_refresh_at`, `description_locally_edited`, `archived_at`, `on_hand`, `reserved`, `available`, `image_url`.

**Notes**

- Sort order: `name ASC, id ASC` (consistent across paged and bare paths) (`parts_core.py:107-118`).
- `image_url` comes from each part's `custom_fields(key="image_url")` row, batched via `image_urls_for_parts` (`_parts_shared.py:28-41`).
- `on_hand` / `reserved` are roll-ups via `bulk_current_quantities`; never compute outside `domain/stock/service.py` (CLAUDE.md, ledger invariant — see [ADR-0001](../adr/0001-append-only-stock-ledger.md)).
- Source: `backend/app/api/routes/parts_core.py:55-135`.

### `POST /api/parts`

Create a new part.

**Request** — `PartIn`. Notable fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | one of name/mpn | Defaults to `mpn` when blank (`parts_core.py:153-154`). |
| `mpn` | string | one of name/mpn | |
| `part_type` | string | yes | `"manual"`, `"linked"`, `"meta"`. |
| `manufacturer`, `internal_part_number`, `description`, `footprint`, `notes_markdown` | string | no | |
| `low_stock_report_quantity`, `attrition_percentage`, `attrition_min_quantity` | numeric | no | |
| `default_storage_location_id` | UUID | no | Validated to belong to this workspace via `assert_in_workspace` (`parts_core.py:176-180`). |
| `default_storage_mandatory`, `serialized` | bool | no | |

**Response** — `201 Created`, body is the serialised part with `on_hand=0, reserved=0`.

**Errors**

- `422` — neither `name` nor `mpn` supplied (`parts_core.py:148-152`).
- `409 Conflict` — pre-check found another live part in this workspace with the same `mpn`. Body includes `existing_id`, `existing_name` (`parts_core.py:156-170`). See [ADR-0004](../adr/0004-mpn-uniqueness-per-workspace.md).

**Notes**

- Source: `backend/app/api/routes/parts_core.py:138-207`.

### `GET /api/parts/{part_id}`

Fetch a single part. Includes archived rows so the detail page can still render (`parts_core.py:212-218`).

**Response** — `200 OK` — `PartOut` with on-hand / reserved roll-ups.

**Errors** — `404 part.not_found` (`_parts_shared.py:95-102`).

**Notes**

- Source: `backend/app/api/routes/parts_core.py:210-218`.

### `PATCH /api/parts/{part_id}`

Update part fields.

**Request** — `PartPatch` (partial). Plus:

| Field | Type | Notes |
|---|---|---|
| `unlink_provider` | bool | Pop-only flag. Clears `linked_provider`, resets `last_refresh_at` and `description_locally_edited`, demotes provider/override custom_fields to `manual` (`parts_core.py:261-280`). |

**Errors**

- `404 part.not_found` — refuses archived parts on writes (BE2-016) (`_parts_shared.py:88-103`).
- `400` — for a `linked_provider != null` part, attempting to change `manufacturer` or `mpn` without `unlink_provider=true` (`parts_core.py:231-237`).
- `404 storage.not_found` — `default_storage_location_id` doesn't belong to this workspace (via `assert_in_workspace`).

**Notes**

- Editing `description` on a linked part flips `description_locally_edited=true` (`parts_core.py:241-246`).
- Source: `backend/app/api/routes/parts_core.py:221-287`.

### `POST /api/parts/{part_id}/archive`

Soft-archive (`archived_at = now`).

**Response** — `200 OK`, `data: null`, `message: "archived"`.

**Notes**

- Uses `require_resource_access(role="admin")` so non-admins probing a foreign workspace's id get `404` rather than `403` (`parts_core.py:297-312`, BE2-009).
- Logs polymorphic counts (attachments, custom_fields, tag_links) and emits audit row `part.archived` (`parts_core.py:316-344`).
- Source: `backend/app/api/routes/parts_core.py:297-345`.

### `POST /api/parts/{part_id}/restore`

Clear `archived_at`. Same admin gate as archive. Emits `part.restored`.

**Notes**

- Source: `backend/app/api/routes/parts_core.py:348-367`.

### `POST /api/parts/bulk-delete`

Soft-archive a list of parts.

**Request** — `BulkDeleteIn`

| Field | Type | Required |
|---|---|---|
| `part_ids` | UUID[] | yes |

**Response** — `200 OK`

```json
{ "data": {
    "archived_ids": [ … ],
    "already_archived_ids": [ … ],
    "not_found_ids": [ … ]
}, "status": { "category": "ok", "message": "archived <N>" } }
```

**Notes**

- Gated `require_role("admin")` (`parts_core.py:370`).
- Rate limit: `30/minute` per workspace (`parts_core.py:371`).
- Cross-workspace ids land in `not_found_ids` deliberately indistinguishably from truly missing ids (`parts_core.py:386-395`).
- Hard-deletion is intentionally not exposed because of FK cascades into `stock_entries`, `lots`, `order_entries`, `bom_entries` (`parts_core.py:381-383`).
- Source: `backend/app/api/routes/parts_core.py:370-441`.

## Bag-signature lookup

### `GET /api/parts/by-bag-signature/{signature}`

Return the most recent stock_entry whose `bag_signature` matches; used by the inline "Found bag" UI in scan-import.

**Path**

| Field | Type | Notes |
|---|---|---|
| `signature` | string | 64-char lowercase hex; otherwise returns `data: null` without querying (`parts_core.py:447-449`). |

**Response** — `200 OK`

```json
{ "data": { "part_id": "…", "lot_id": "…" | null, "storage_location_id": "…" | null, "quantity": 25 } | null, "status": { … } }
```

**Notes**

- Source: `backend/app/api/routes/parts_core.py:452-480`.
- See CLAUDE.md "bag_signature" invariant.

## Stock & lots roll-up

### `GET /api/parts/{part_id}/stock`

Return a per-(storage, lot) breakdown plus a total.

**Response** — `200 OK`

```json
{ "data": {
    "total_on_hand": 250,
    "rows": [ { "storage_location_id": "…" | null, "lot_id": "…" | null, "quantity": 100 }, … ]
}, "status": { … } }
```

**Notes**

- Includes archived parts (`parts_core.py:485`).
- Source: `backend/app/api/routes/parts_core.py:483-499`.

### `GET /api/parts/{part_id}/lots`

List lots for the part.

**Response** — `200 OK` — array of `{ id, name, serial_number, purchase_quantity, purchase_unit_cost, purchase_currency, expiration_date, comments, parent_lot_id, source_type, created_at }` (`parts_core.py:511-528`).

**Notes**

- Sorted `created_at DESC`.
- Source: `backend/app/api/routes/parts_core.py:502-528`.

## Substitutes

### `POST /api/parts/{part_id}/substitutes`

Add a substitute binding.

**Request** — `SubstituteIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `substitute_part_id` | UUID | yes | |
| `direction` | string | yes | TODO(verify): exact set of allowed values. |

**Errors** — `404 part.not_found` if either side is archived or absent (`parts_core.py:537-538`).

**Notes**

- Source: `backend/app/api/routes/parts_core.py:531-540`.

### `GET /api/parts/{part_id}/substitutes`

List substitutes (allowed on archived parts).

**Notes**

- Source: `backend/app/api/routes/parts_core.py:543-547`.

### `DELETE /api/parts/{part_id}/substitutes/{substitute_id}`

Remove a binding (allowed on archived parts so dead bindings can be cleaned up).

**Notes**

- Source: `backend/app/api/routes/parts_core.py:550-558`.

## Meta-part members

### `GET /api/parts/{meta_id}/members`

List the members of a meta-part.

**Notes**

- Source: `backend/app/api/routes/parts_core.py:564-572`.

### `POST /api/parts/{meta_id}/members`

Add a member.

**Request** — `MetaMemberIn`

| Field | Type | Required |
|---|---|---|
| `member_part_id` | UUID | yes |

**Response** — `201 Created` (or `200 OK` if already linked — see notes).

**Errors**

- `400` — meta part is not `part_type="meta"` (`parts_core.py:578-579`).
- `400` — `member_part_id == meta_id` (`parts_core.py:581-582`).
- `400` — member is itself a meta part (`parts_core.py:583-584`).

**Notes**

- Idempotent: existing binding returns `200 OK` with the existing row (`parts_core.py:585-595`).
- Source: `backend/app/api/routes/parts_core.py:575-599`.

### `DELETE /api/parts/{meta_id}/members/{member_id}`

Remove a member binding (allowed on archived meta).

**Notes**

- Source: `backend/app/api/routes/parts_core.py:602-609`.

## Part activity feed

### `GET /api/parts/{part_id}/activity`

Combined timeline of stock entries plus synthetic `part_created` / `part_updated` items.

**Query**

| Field | Type | Notes |
|---|---|---|
| `limit` | int | `_DEFAULT_LIMIT`/`_MAX_LIMIT` from `_activity.py`. TODO(verify): the exact integer values. |
| `before_occurred_at` | ISO-8601 | Cursor timestamp; missing returns the head page (`parts_core.py:619`). |
| `before_id` | UUID | Cursor tiebreak. |

**Response** — `200 OK` — built by `build_activity` (`backend/app/api/routes/_activity.py`); shape includes the merged item array plus paging metadata. Synthetic events are only included on the head page (`parts_core.py:666`).

**Errors**

- `422` — `before_occurred_at` is not parseable ISO-8601 (`parts_core.py:629-631`).

**Notes**

- Allowed on archived parts (`parts_core.py:623`).
- Source: `backend/app/api/routes/parts_core.py:612-669`.

## Provider assets

### `GET /api/parts/assets/{ws_id}/{filename}`

Serve a content-addressed provider asset. Served with `Cache-Control: public, max-age=31536000, immutable` and `X-Content-Type-Options: nosniff` (`parts_assets.py:91-97`).

**Path**

| Field | Type | Notes |
|---|---|---|
| `ws_id` | UUID | Must equal the caller's current workspace, else `404` (`parts_assets.py:75-76`). |
| `filename` | string | Flat name, no `/`, `\`, or leading `.` (`parts_assets.py:78-79`). |

**Query**

| Field | Type | Notes |
|---|---|---|
| `name` | string ≤ 120 | Save-As filename for the `Content-Disposition` header (`parts_assets.py:98-106`). |

**Response — image extensions** (`jpg`, `jpeg`, `png`, `gif`, `webp`) — served `inline`. Datasheets (`pdf`) and unknown extensions (served as `application/octet-stream`) are forced `attachment` (`parts_assets.py:54-62`, `parts_assets.py:109-117`).

**Errors**

- `404` — wrong workspace, file missing on disk (`parts_assets.py:75-83`).
- `400` — invalid filename (`parts_assets.py:78-79`).

**Notes**

- SVG is intentionally excluded from the inline list (SEC2-006 / SEC2-011) (`parts_assets.py:53-62`).
- Source: `backend/app/api/routes/parts_assets.py:65-118`.

### `POST /api/parts/{part_id}/refresh-from-provider`

Re-run the workspace's MPN lookup against this part and reconcile `source='provider'` custom_fields (insert / update / delete). Always touches `manufacturer`, `mpn`, `footprint`; `description` only when not locally edited.

**Response — found** — `200 OK`

```json
{ "data": {
    "found": true,
    "provider": "mouser",
    "summary": { "added": 3, "updated": 2, "removed": 1 },
    "part": <PartOut>
}, "status": { … } }
```

**Response — no match** — `200 OK` with `{ "found": false, "message": "…", "provider": "mouser" }` (`parts_assets.py:160-167`).

**Errors**

- `400` — part has no MPN (`parts_assets.py:142-143`).
- `400` — workspace has no provider configured (`parts_assets.py:150-154`).

**Notes**

- Rate limit: `60/minute` per workspace (`parts_assets.py:128`).
- Uses `lookup_fresh` (not the cache) because the operator explicitly asked (`parts_assets.py:156-159`).
- Reconciliation rules per spec key:
  - existing `source='provider'` → update value (`parts_assets.py:239-243`).
  - existing `source='manual'` → leave alone (user owns it).
  - existing `source='override'` → leave value, refresh `original_value` so Restore reverts to current upstream (`parts_assets.py:244-250`).
  - absent → insert as `source='provider'` (`parts_assets.py:225-238`).
- Provider assets (`image_url`, `datasheet_url`) downloaded locally via `fetch_provider_asset`; failure leaves the upstream URL (`parts_assets.py:205-210`).
- Source: `backend/app/api/routes/parts_assets.py:127-273`.

## Provider lookup

### `POST /api/parts/lookup-mpn`

Cached MPN lookup against the workspace's provider. Used by the Add-Part wizard.

**Request** — `LookupIn`

| Field | Type | Required |
|---|---|---|
| `mpn` | string | yes |

**Response** — `200 OK`

```json
{ "data": { "found": bool, "result": { … } | null, "message": "…", "provider": "mouser" }, "status": { … } }
```

When no provider is configured the response is `{ found: false, result: null, message: "no provider configured (set one in Workspace settings)", provider: ws.parts_provider or "none" }` — i.e. a `200`, not an error (`parts_provider.py:28-34`).

**Notes**

- Rate limit: `60/minute` per workspace (`parts_provider.py:19`).
- Uses `lookup_with_cache` (`parts_provider.py:35`).
- Source: `backend/app/api/routes/parts_provider.py`.

## Scan import

### `POST /api/parts/bulk-import-from-scan`

Materialise a batch of scanned bag rows into Parts (and optional initial stock).

**Request** — `ScanImportIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `rows` | `ScanImportRow[]` | yes | Capped at 50 by Pydantic (`parts_scan.py:112`). |
| `idempotency_key` | string | no | When set, identical resubmits return the cached envelope (`parts_scan.py:129-156`). When absent, a SHA-256 content hash of all rows is used internally — but no cache HIT path runs without an explicit key, to preserve the duplicate-detection flow on a second scan of the same MPN (`parts_scan.py:144-156`). |

`ScanImportRow` fields: `mpn`, `quantity`, `storage_location_id`, `bag_signature`, `raw_bag_code`, `lot_name`, `lot_serial`, `comments`. TODO(verify): exhaustive list and which are optional.

**Response** — `200 OK`

```json
{ "data": {
    "rows": [ { "mpn": "…", "status": "created" | "duplicate" | "bag_rescan" | "bag_signature_mismatch" | "lookup_failed" | "invalid" | "row_failed" | "deadline_exceeded", … } ],
    "summary": { "created": N, "duplicate": N, "bag_rescan": N, "bag_signature_mismatch": N, "lookup_failed": N, "invalid": N, "row_failed": N, "deadline_exceeded": N },
    "provider": "mouser"
}, "status": { … } }
```

Per-row extras:
- `created` → `part_id`, `quantity_added`, `stock_error?` (`parts_scan.py:367-373`).
- `duplicate` → `part_id` of the existing live part (`parts_scan.py:275-281`).
- `bag_rescan` → `part_id`, `lot_id`, `storage_location_id`, `quantity` from the prior entry (`parts_scan.py:253-264`).
- `lookup_failed`, `invalid`, `row_failed`, `deadline_exceeded`, `bag_signature_mismatch` → `error: string`.

**Errors**

- `400` — workspace has no provider configured (`parts_scan.py:166-170`).

**Notes**

- Rate limit: `5/minute` per workspace (`parts_scan.py:80`).
- Per-row writes are wrapped in SAVEPOINTs (`db.begin_nested`) so a single row failure doesn't roll back the batch (`parts_scan.py:343-365`). `db.commit()` is called explicitly before returning (`parts_scan.py:429`); see the in-source comment for the partial-commit hardening (`parts_scan.py:404-417`).
- Per-row provider lookup runs in a function-scope `ThreadPoolExecutor(max_workers=2)` with an 8 s per-row timeout and a 60 s wall-clock budget (`parts_scan.py:57-60`, `parts_scan.py:175-189`, `parts_scan.py:298-314`).
- Idempotency cache row uses `INSERT … ON CONFLICT DO NOTHING` to avoid unwinding the outer tx on race (`parts_scan.py:418-427`).
- Source: `backend/app/api/routes/parts_scan.py:79-430`.

### `POST /api/parts/{part_id}/quick-remove-bag`

Consume from a previously-imported bag from the scan-import re-scan UI.

**Request** — `QuickRemoveBagIn`

| Field | Type | Required |
|---|---|---|
| `quantity` | int | yes |
| `storage_location_id` | UUID | TODO(verify) |
| `lot_id` | UUID | TODO(verify) |
| `comments` | string | no |

**Response** — `200 OK`, `data: null`, `message: "removed"`.

**Errors**

- `400` — `StockError` from `remove_stock`, e.g. over-quantity request (`parts_scan.py:593-594`).

**Notes**

- Delegates to `domain/stock/service.py::remove_stock` (`parts_scan.py:578-592`).
- Source: `backend/app/api/routes/parts_scan.py:563-595`.

## TODOs

- TODO(verify): `SubstituteIn.direction` allowed values — not enumerated in the route handler.
- TODO(verify): `_DEFAULT_LIMIT` / `_MAX_LIMIT` exact values for the activity endpoint (defined in `_activity.py`).
- TODO(verify): exhaustive `ScanImportRow` field list and per-field optionality (defined in `domain/parts/schemas.py`).
- TODO(verify): `QuickRemoveBagIn` field optionality (defined in `domain/parts/schemas.py`).
