# Builds API

Audience: engineer

Builds (a planned/in-progress run that consumes BOM stock and produces an output lot), reservations, shortage analysis, multi-stage builds, and the consume actions that close a build.

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
- A `quantity` change is **refused** (`400 build.read_only`) once any stage of the build has been consumed — the rebalance would re-reserve material a completed stage already drew. See [Multi-stage builds](#multi-stage-builds).

A multi-stage build also walks `planned → in_progress` on its first stage consume and `→ complete` when its last stage lands.

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

`shortage` is a list of per-entry rows from `shortage_analysis(db, ws.id, project, build_quantity=b.quantity)` (`backend/app/domain/builds/service.py`). Each row:

```json
{ "project_entry_id": "…", "part_id": "…", "part_name": "…",
  "attrition_pct": 2.5, "required": 103, "available": 100,
  "substitute_ids": ["…"], "substitute_available": 0, "short_by": 3 }
```

`required` is the effective, attrition-adjusted, **ceil-rounded integer** demand (part-intrinsic attrition × per-BOM-line `attrition_pct`, then rounded up — see [`builds-and-bom.md`](../domain/builds-and-bom.md#required-quantity-formula)). `attrition_pct` is the line's waste rate, surfaced so the UI can show what inflated `required`.

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

**Errors** — `400 build.has_stages` when the build has one or more stages: consume each stage through `POST /api/builds/{build_id}/stages/{stage_id}/consume` instead. Allowing both would draw every stage's stock at once while leaving the stages reported as un-built.

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

## Multi-stage builds

Track B2. A build may be assembled across several stages, each consuming a defined subset (and portion) of the BOM. A build with **no** stages is a single-pass build and every endpoint above behaves exactly as it did before this feature.

**Reservations are taken once, up front, by `POST /api/builds`.** Creating a stage writes no ledger row; each stage consume releases only the slice it consumes, so nothing is double-counted across stages. Domain detail: [`builds-and-bom.md`](../domain/builds-and-bom.md#multi-stage-builds).

These three routes live in `backend/app/api/routes/build_stages.py`, mounted under the same `/api/builds` prefix. They were split out of `builds.py` to stay inside that module's 300-line `line-count-budget` cap — the same reason `parts_core` / `parts_scan` / `parts_assets` are separate modules.

### `GET /api/builds/{build_id}/stages`

List the build's active stages, in consumption order, each with its lines and a per-stage shortage analysis.

**Response** — `200 OK` — array of:

```json
{ "id": "…", "build_id": "…", "name": "SMT reflow", "sequence": 0,
  "status": "planned",
  "started_at": null, "completed_at": null, "comments": null,
  "lines": [{ "id": "…", "project_entry_id": "…", "portion_pct": 50.0 }],
  "shortage": [
    { "project_entry_id": "…", "part_id": "…", "part_name": "R1k",
      "attrition_pct": 25.0, "portion_pct": 50.0,
      "required": 69, "available": 400,
      "substitute_ids": [], "substitute_available": 0, "short_by": 0 }
  ],
  "created_at": "…", "updated_at": "…" }
```

`shortage` mirrors the whole-build shape (so one UI component renders both) with `portion_pct` added. `required` is **this stage's slice** of the whole-build, attrition-adjusted `_required` value — see the allocation formula in [`builds-and-bom.md`](../domain/builds-and-bom.md#per-stage-requirement-allocation). Empty array for a single-pass build.

**Errors** — `404 build.not_found`.

### `POST /api/builds/{build_id}/stages`

Create a stage. Writes **no** ledger rows.

**Request** — `BuildStageCreateIn`:

| Field | Type | Notes |
|---|---|---|
| `name` | str | 1–200 chars. |
| `sequence` | int? | Consumption order. Defaults to "append after the current last stage". |
| `comments` | str? | |
| `lines` | list | ≥ 1 of `{ project_entry_id, portion_pct? }`. `portion_pct` defaults to `100`, must be `> 0` and `<= 100`. |

**Response** — `201 Created` — the created stage, in the same shape as the list route.

**Errors**

- `404 build.not_found` / `404 project.not_found`.
- `400 build_stage.error`:
  - `"build is complete"` / `"build is cancelled"` / `"build is archived"`.
  - `"project entry <id> not in this project"` — including a BOM entry from another workspace.
  - `"project entry <id> has no part to consume"` (`non_part` / `unmatched` / NULL `part_id`).
  - `"project entry <id> is DNP"`.
  - `"project entry <id> listed twice in this stage"`.
  - `"project entry <id> is over-committed (<n>% across stages; max 100%)"`.
  - `"stage sequence <n> already used by this build"`.
- `409` — workspace-isolation trigger (`WS001`) via `raise_integrity_as_409`.

**Audit** — writes `build_stage.created` (`target_type="build_stage"`).

### `POST /api/builds/{build_id}/stages/{stage_id}/consume`

Consume one stage. All-or-nothing.

**Request** — `StageConsumeIn`: same shape as `ConsumeIn` — `lines` of `{ project_entry_id, part_id, quantity, lot_id?, storage_location_id? }`, plus `output_storage_location_id?` / `output_lot_name?`. The output fields only take effect on the stage that completes the build; a staged build produces its sub-assembly lot once, not once per stage.

**Response** — `200 OK`

```json
{ "build_id": "…", "build_stage_id": "…",
  "stage_status": "complete", "build_status": "in_progress",
  "consumed_entries": ["…"], "remaining_stages": 1,
  "output_lot_id": null, "output_stock_entry_id": null }
```

The emitted `build_consume` ledger rows carry `build_stage_id`, so `GET /api/builds/{build_id}/activity` shows which stage took what.

**Errors**

- `404 build.not_found`; `404 build_stage.not_found` — unknown stage, a stage of a *different* build, an archived stage, or a stage in another workspace.
- `400 build.consume_error`:
  - `"build is <status>"` / `"build is archived"`.
  - `"stage '<name>' is already complete"`.
  - `"stage '<name>' (sequence <n>) must be consumed before '<name>'"` — stages are consumed in `sequence` order.
  - `"stage '<name>' has nothing to consume"`.
  - `"project entry <id> is not in this stage"`.
  - every `BuildError` the whole-build consume can raise (substitute/lot/storage validation, insufficient stock, under-consumed coverage) — the two paths share `apply_consume_lines`.
- `409 stock.conflict_error` — output storage violates `single_part_only` / `existing_parts_only` on the final stage.

**Audit** — writes `build_stage.consumed` (`target_type="build_stage"`).

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

- TODO(verify): `BuildCreateIn`, `BuildPatchIn`, `ConsumeIn` exact field lists (`domain/builds/schemas.py`). `BuildStageCreateIn` / `StageConsumeIn` are documented above from the schema.
- TODO(verify): full set of `build.status` values and the transitions allowed via PATCH.
- TODO(verify): `shortage_analysis` return shape (`domain/builds/service.py:77`).
- TODO(verify): exact insufficient-stock error messages from `consume` (`service.py:392`, `service.py:425`).
- TODO(verify): `consume` response dict shape.
