# Parts API

Audience: engineer

Parts CRUD, provider asset serving, MPN lookup, and the scan-import pipeline. Combines five routers — `parts_core`, `parts_assets`, `parts_refresh`, `parts_scan`, `parts_provider` — that all mount at `/api/parts` (`backend/app/main.py:367-392`).

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

## Replace part across projects

### `POST /api/parts/{part_id}/replace-in-projects`

Repoint every matching BOM line from `{part_id}` to a replacement part
across some or all of the workspace's projects, in one transaction
(mirrors PartsBox's replace-across-projects action).

**Request** — `ReplaceInProjectsIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `target_part_id` | UUID | yes | The replacement part. Must be a live (non-archived) part in the workspace. |
| `project_ids` | UUID[] | no | Limit the replacement to these projects. Omitted or empty ⇒ every **active** project in the workspace. Max 1000. |

Every `project_entries` row with `part_id == {part_id}` inside the
selected projects is updated to `target_part_id`. The path part (source)
may be archived — you commonly replace *because* a part was retired — so
it is resolved with `include_archived=True`; the target must be live
(binding an archived part into a BOM is the BE2-016 vector guarded on the
add/patch/match-entry routes).

`project_entries` has no `(project_id, part_id)` uniqueness constraint, so
no BOM line is skipped or merged — every matching line is repointed.

**Response** — `200 OK`

```json
{ "data": { "updated_entries": 3, "affected_projects": 2 }, "status": {"category": "ok", "message": "OK"} }
```

`updated_entries` is the total number of BOM lines repointed;
`affected_projects` counts the distinct projects that had at least one
line changed.

**Errors**

- `400` (`part.replace_same_target`) — `target_part_id == {part_id}`.
- `404` — source part, target part, or any named `project_ids` entry is
  not in the caller's workspace (or the target is archived). The whole
  operation rolls back — no partial writes.

**Audit**

- One `project.part_replaced` row per affected project
  (`target_ids=[project_id, source_id, target_id]`, `comment="entries=N"`).
- One `part.replaced_in_projects` summary row on the source part
  (`comment="projects=N entries=M"`).

**Notes**

- Route: `backend/app/api/routes/parts_relations.py`.
- Service: `backend/app/domain/projects/replace_part.py::replace_part_in_projects`.

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

Serve a content-addressed provider asset. Served with `Cache-Control: public, max-age=31536000, immutable` and `X-Content-Type-Options: nosniff` (`parts_assets.py:92-98`).

**Path**

| Field | Type | Notes |
|---|---|---|
| `ws_id` | UUID | Must equal the caller's current workspace, else `404` (`parts_assets.py:63-64`). |
| `filename` | string | Flat name, no `/`, `\`, or leading `.` (`parts_assets.py:70-71`). |

**Query**

| Field | Type | Notes |
|---|---|---|
| `name` | string ≤ 120 | Save-As filename for the `Content-Disposition` header (`parts_assets.py:99-110`). |

**Response — image extensions** (`jpg`, `jpeg`, `png`, `gif`, `webp`) — served `inline`. Datasheets (`pdf`) and unknown extensions (served as `application/octet-stream`) are forced `attachment` (`parts_assets.py:42-50`, `parts_assets.py:90-114`).

**Errors**

- `404` — wrong workspace, file missing on disk (`parts_assets.py:63-84`).
- `400` — invalid filename (`parts_assets.py:70-76`).

**Notes**

- SVG is intentionally excluded from the inline list (SEC2-006 / SEC2-011) (`parts_assets.py:41-50`).
- Source: `backend/app/api/routes/parts_assets.py:53-122`.

### `POST /api/parts/{part_id}/refresh-from-provider`

Re-run an MPN lookup against this part and reconcile its `source='provider'` custom_fields (insert / update / delete).

**Query params**

| Param | Meaning |
|-------|---------|
| `provider` | Which configured provider to refresh from. Omitted, or naming the workspace's own `parts_provider`, runs the PRIMARY flow. Any other known provider runs as a SECONDARY. |

The two tiers differ in what they may write — see ADR-0031.

- **Primary** — always touches `manufacturer`, `mpn`, `footprint` (and `description` when not locally edited), sets `parts.linked_*`, and owns the un-namespaced custom fields.
- **Secondary** — writes **no part column at all**. It records a `part_provider_links` row and custom fields under its own `"{provider}:"` prefix: `{provider}:source_url`, `{provider}:datasheet_url`, `{provider}:category`, and one `{provider}:{key}` per upstream spec. Assets are not downloaded; the upstream URL is stored as-is.

**Response — found** — `200 OK`

```json
{ "data": {
    "found": true,
    "provider": "mouser",
    "summary": { "added": 3, "updated": 2, "removed": 1, "skipped": 0 },
    "link": { "provider": "mouser", "external_id": "…", "source_url": "…", "last_refresh_at": "…" },
    "part": <PartOut>
}, "status": { … } }
```

**Response — no match** — `200 OK` with `{ "found": false, "message": "…", "provider": "mouser" }`. No link row is created for a miss.

**Errors**

- `400` — part has no MPN.
- `400` `part.provider_not_configured` — no primary provider, or no credentials for the named secondary (the body carries `provider`).
- `422` `part.provider_unknown` — a provider name `make_provider` doesn't know.

**Notes**

- Rate limit: `60/minute` per workspace.
- Uses `lookup_fresh` (not the cache) because the operator explicitly asked.
- Reconciliation rules per spec key:
  - existing `source='provider'` → update value.
  - existing `source='manual'` → leave alone (user owns it).
  - existing `source='override'` → leave value, refresh `original_value` so Restore reverts to current upstream.
  - absent → insert as `source='provider'`.
- **Non-interference:** each reconciliation is scoped to its own namespace — the primary sees only un-namespaced keys, a secondary only its own prefix — so the trailing "delete rows absent from my payload" pass can never touch another provider's rows (`provider_fields.py::provider_owns_custom_field_key`).
- `summary.skipped` counts secondary fields dropped because the namespaced key would overflow `custom_fields.key` (varchar 256) — the prefix adds characters to an upstream name we don't control, and truncating a key would collide two attributes onto one row. Always `0` on the primary path, which writes bare keys.
- Provider assets (`image_url`, `datasheet_url`) are downloaded locally via `fetch_provider_asset` on the primary path only; failure leaves the upstream URL.
- Source: `backend/app/api/routes/parts_refresh.py`.

### `DELETE /api/parts/{part_id}/provider-links/{provider}`

Unlink a **secondary** provider from a part. Drops its `part_provider_links` row, deletes its namespaced `source='provider'` fields, and demotes its `override` rows to plain `manual` (the user edited those, so they survive as their own).

**Response** — `200 OK`

```json
{ "data": {
    "provider": "mouser",
    "removed_fields": 5,
    "provider_links": [ { "provider": "digikey", … } ]
}, "status": { … } }
```

**Errors**

- `400` `part.provider_link_is_primary` — the named provider is the workspace's `parts_provider`. Use `PATCH /api/parts/{id}` with `unlink_provider=true`, which also releases the part columns. The guard reads `ws.parts_provider`, not the part's sticky `linked_provider`, so a link stays removable after an admin switches the workspace primary away from it (ADR-0031).
- `404` `part.provider_link_not_found` — no live link for that provider (also the cross-workspace answer).

**Notes**

- Rate limit: `60/minute` per workspace. Member+ (router gate).
- Audit: `part.provider_unlinked`, comment `provider=<name>`.
- Nothing outside the `"{provider}:"` namespace is touched.
- Source: `backend/app/api/routes/parts_refresh.py`.

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
