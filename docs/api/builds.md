# Builds API

Audience: engineer

Builds (a planned/in-progress run that consumes BOM stock and produces an output lot), reservations, shortage analysis, and the consume action that closes a build.

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. Mounted at `/api/builds` (`backend/app/main.py:375`). Reservations and consumption emit ledger rows tagged with `build_id` — see [ADR-0001](../adr/0001-append-only-stock-ledger.md).

## Status machine

`build.status` values seen in the code:

| State | Trigger |
|---|---|
| `planned` | Created (initial); reservations applied (`builds.py:75-94`). |
| `in_progress` | TODO(verify): set via `PATCH /api/builds/{build_id}` `status` field. |
| `complete` | Set by `consume()` on success (`service.py:474`). Read-only thereafter unless transitioning to `cancelled` (`builds.py:114-115`). |
| `cancelled` | Set via PATCH; releases reservations (`builds.py:118`, `builds.py:125-126`). |

Reservation-rebalancing rules on PATCH (`builds.py:117-132`):

- `quantity` change while `status in ("planned", "in_progress")` → `release_reservations` then `apply_reservations` against the new quantity.
- Transition into `cancelled` → `release_reservations`.

## Routes

### `GET /api/builds`

List builds.

**Query**

| Field | Type | Notes |
|---|---|---|
| `archived` | bool | Default `false`. |
| `project_id` | UUID | Filter to one project. |
| `limit` | int | Default `200`, max `1000`. |

**Response — build shape** —

```json
{ "id": "…", "name": "…", "project_id": "…", "quantity": 10,
  "status": "planned", "started_at": "…" | null, "completed_at": "…" | null,
  "output_lot_id": "…" | null, "comments": "…",
  "archived_at": "…" | null,
  "created_at": "…", "updated_at": "…" }
```

**Notes**

- Sorted `created_at DESC`.
- Source: `backend/app/api/routes/builds.py:59-72`.

### `POST /api/builds`

Create a build and immediately apply reservations against the project's BOM.

**Request** — `BuildCreateIn`: `name`, `project_id`, `quantity`, `comments?`. TODO(verify): exhaustive optionality.

**Response** — `201 Created` — serialised build.

**Errors** — `404 project not found` (`builds.py:52-56`).

**Notes**

- `apply_reservations` walks the BOM and writes reservation rows (`status="reserved"` ledger entries) (`builds.py:91-93`).
- Source: `backend/app/api/routes/builds.py:75-94`.

### `GET /api/builds/{build_id}`

Fetch the build plus a fresh shortage analysis.

**Response** — `200 OK`

```json
{ "data": {
    "build": <Build>,
    "shortage": <ShortageAnalysisResult>
}, "status": { … } }
```

`shortage` is whatever `shortage_analysis(db, ws.id, project, build_quantity=b.quantity)` returns (`backend/app/domain/builds/service.py:77`). TODO(verify): exact shape (per-entry required vs available, candidate parts).

**Errors** — `404 build not found` (`builds.py:45-49`).

**Notes**

- Source: `backend/app/api/routes/builds.py:97-108`.

### `PATCH /api/builds/{build_id}`

Update editable fields and re-balance reservations.

**Request** — `BuildPatchIn` (partial): typically `name`, `quantity`, `status`, `comments`. TODO(verify): full editable field list.

**Errors**

- `400` — `status == "complete"` and the patch isn't transitioning to `"cancelled"` (`builds.py:114-115`).

**Notes**

- See "Status machine" above for the reservation-rebalance rules.
- Source: `backend/app/api/routes/builds.py:111-134`.

### `POST /api/builds/{build_id}/archive`

Release reservations and soft-archive (admin gate via `require_resource_access`).

**Notes**

- Source: `backend/app/api/routes/builds.py:140-147`.

### `POST /api/builds/{build_id}/restore`

Clear `archived_at` (admin gate). Reservations are NOT re-applied.

**Notes**

- Source: `backend/app/api/routes/builds.py:150-156`.

### `POST /api/builds/{build_id}/consume`

Apply consumption: release reservations, write negative ledger entries against the chosen lots/storage, optionally produce an output lot for the project's sub-assembly part. Sets `build.status = "complete"`.

**Request** — `ConsumeIn`. TODO(verify): exact shape — appears to include per-line `{ project_entry_id, part_id, lot_id?, storage_location_id?, quantity }` plus optional output `{ output_storage_location_id?, output_lot_name? }` based on errors below.

**Response** — `200 OK` — service result dict (TODO(verify): shape — likely `{ build_id, status, stock_entries, output_lot_id }`).

**Errors** — `400` (`BuildError` mapped, `builds.py:169-172`):

- `"build is <status>"` — non-resumable state (`service.py:274`).
- `"project entry <id> not in this project"` (`service.py:347`).
- `"project entry <id> has no part to consume"` (`service.py:349`).
- `"project entry <id> is DNP"` (`service.py:351`).
- `"entry <id> has missing part"` (`service.py:358`).
- `"part <id> is not <kind> for entry <id>"` (`service.py:365`).
- `"lot <id> not in workspace"` (`service.py:376`).
- `"storage <id> not in workspace"` (`service.py:380`).
- Insufficient stock / over-consume — TODO(verify): exact wording (`service.py:392`, `service.py:425`).
- `"project's sub-assembly part not in workspace"` (`service.py:435`).
- `"output storage not in workspace"` / `"output storage archived or full"` (`service.py:441-443`).
- `409 stock.conflict_error` — output storage violates `single_part_only` or `existing_parts_only` constraints; raised by `enforce_storage_constraints` (`backend/app/domain/stock/service.py:330`) before the output-lot insert (PR #299, issue #280). Body extras: `{ message, constraint, storage_location_id }` where `constraint` is `"single_part_only"` or `"existing_parts_only"`. Note this is a `StockConflictError` mapped to `409`, not an `OrderError`/`BuildError`.

**Notes**

- Releases outstanding reservations before applying the per-line writes (`service.py:303-305`).
- Source: `backend/app/api/routes/builds.py:159-173`.
- Service: `backend/app/domain/builds/service.py:263-` (extends past `:474`).

### `GET /api/builds/{build_id}/activity`

Combined timeline of `stock_entries` tagged with this `build_id` plus synthetic `build_created` / `build_updated` items.

**Query**

| Field | Type | Notes |
|---|---|---|
| `limit` | int | `_DEFAULT_LIMIT`/`_MAX_LIMIT`. |
| `before_occurred_at` | ISO-8601 | Cursor; `422` on parse failure. |
| `before_id` | UUID | Cursor tiebreak. |

**Response** — `200 OK` — `build_activity` (the same `build_activity` import alias that the activity helper uses); shape parallels `/api/parts/{part_id}/activity` and `/api/orders/{order_id}/activity`. Synthetic events only on the head page.

**Notes**

- Source: `backend/app/api/routes/builds.py:176-229`.

## TODOs

- TODO(verify): `BuildCreateIn`, `BuildPatchIn`, `ConsumeIn` exact field lists (`domain/builds/schemas.py`).
- TODO(verify): full set of `build.status` values and the transitions allowed via PATCH.
- TODO(verify): `shortage_analysis` return shape (`domain/builds/service.py:77`).
- TODO(verify): exact insufficient-stock error messages from `consume` (`service.py:392`, `service.py:425`).
- TODO(verify): `consume` response dict shape.
