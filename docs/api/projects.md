# Projects & BOM API

Audience: engineer

Project (BOM owner) CRUD, BOM entries, the BOM-import wizard (`preview` + `commit`), and reusable import presets. Two routers: `/api/projects` (`backend/app/main.py:373`) and `/api/bom-presets` (`backend/app/main.py:377`).

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. Live-part guard (`_assert_part_live`) refuses bindings against archived parts; archived-but-real returns `404 part.not_found` (BE2-016, `projects.py:180-187`).

## Project CRUD

### `GET /api/projects`

List projects.

**Query**

| Field | Type | Notes |
|---|---|---|
| `archived` | bool | Default `false`. |
| `q` | string | ILIKE on `name`, `description`. |
| `limit` | int | Default `200`, max `1000`. |

**Response** — `200 OK` — array of:

```json
{ "id": "…", "name": "…", "description": "…", "notes_markdown": "…",
  "associated_subassembly_part_id": "…" | null,
  "archived_at": "…" | null, "created_at": "…", "updated_at": "…" }
```

**Notes**

- Sorted `updated_at DESC`.
- Source: `backend/app/api/routes/projects.py:62-76`.

### `POST /api/projects`

Create a project.

**Request** — `ProjectCreateIn`: `name`, `description?`, `notes_markdown?`, `associated_subassembly_part_id?`. The associated part is validated via `_assert_part_live` (`projects.py:81-82`).

**Response** — `201 Created` — serialised project.

**Errors** — `404 part.not_found` if `associated_subassembly_part_id` is missing or archived.

**Notes**

- Source: `backend/app/api/routes/projects.py:79-94`.

### `GET /api/projects/{project_id}`

Fetch a project.

**Errors** — `404 project.not_found` (`projects.py:97-101`).

**Notes**

- Source: `backend/app/api/routes/projects.py:104-106`.

### `PATCH /api/projects/{project_id}`

Update editable fields.

**Request** — `ProjectPatchIn` (partial). Same `_assert_part_live` guard on `associated_subassembly_part_id`.

**Notes**

- Source: `backend/app/api/routes/projects.py:109-118`.

### `POST /api/projects/{project_id}/archive`

Soft-archive (`archived_at = now`). Admin-gated via `require_resource_access`. Logs polymorphic counts for attachments / custom_fields / tag_links.

**Notes**

- Source: `backend/app/api/routes/projects.py:124-156`.

### `POST /api/projects/{project_id}/restore`

Clear `archived_at`. Admin-gated.

**Notes**

- Source: `backend/app/api/routes/projects.py:159-165`.

## BOM entries

### `GET /api/projects/{project_id}/entries`

List BOM entries for the project, sorted by `order_index`.

**Response — entry shape** —

```json
{ "id": "…", "project_id": "…", "entry_type": "part" | "meta" | "placeholder",
  "part_id": "…" | null, "meta_part_id": "…" | null,
  "name": "…", "quantity": 4.0, "comments": "…",
  "designators": ["R1", "R2"], "cad_footprint": "…", "cad_key": "…",
  "dnp": false, "order_index": 12 }
```

**Notes**

- Source: `backend/app/api/routes/projects.py:169-177`.

### `POST /api/projects/{project_id}/entries`

Append a BOM entry. `order_index` is auto-set to `max(order_index)+1` (`projects.py:199-207`).

**Request** — `BomEntryIn`. TODO(verify): full field list and `entry_type` allowed values.

**Errors** — `404 part.not_found` if `part_id` or `meta_part_id` is missing or archived (`projects.py:195-198`).

**Notes**

- Source: `backend/app/api/routes/projects.py:190-227`.

### `PATCH /api/projects/{project_id}/entries/{entry_id}`

Update an entry.

**Request** — `BomEntryPatch` (partial).

**Errors** — `404` for missing entry or part (live-part guard) (`projects.py:230-244`).

**Notes**

- Uses `assert_child_in_parent` to enforce that `entry.project_id == project_id` before any work (`projects.py:233`).
- Source: `backend/app/api/routes/projects.py:230-244`.

### `DELETE /api/projects/{project_id}/entries/{entry_id}`

Hard-delete the entry.

**Notes**

- Source: `backend/app/api/routes/projects.py:247-252`.

### `POST /api/projects/{project_id}/entries/{entry_id}/match`

Bind an existing live part to a placeholder entry. Sets `entry_type = "part"` and `part_id`.

**Request** — `MatchEntryIn`: `part_id`.

**Errors** — `404 part.not_found` for missing or archived parts (`projects.py:276-280`).

**Notes**

- Source: `backend/app/api/routes/projects.py:272-284`.

## BOM import wizard

The frontend invokes `preview` to render a diff, then `commit` to apply it. Both run server-side via `domain/projects/bom_import.py`.

### `POST /api/projects/{project_id}/bom/preview`

Parse a CSV / paste against a column-mapping config and return the proposed entries (no DB writes). The response model is whatever `bom.preview` returns (`BomImportPreview`).

**Request** — `BomImportPreviewIn`. TODO(verify): exact field shape (CSV bytes vs rows; column-mapping config; preset reference).

**Response** — `200 OK` — `model_dump()` of the preview result.

**Notes**

- Source: `backend/app/api/routes/projects.py:256-259`.
- Service: `backend/app/domain/projects/bom_import.py::preview`.

### `POST /api/projects/{project_id}/bom/import`

Commit the preview into `project_entries` rows.

**Request** — `BomImportCommitIn`. TODO(verify): exact field shape and preview→commit handoff (token? full payload?).

**Response** — `200 OK` — `model_dump()` of the commit result (counts, conflicts, skipped rows).

**Notes**

- The dep rolls back on any raise, so no explicit try/except is needed (`projects.py:265-267`).
- Source: `backend/app/api/routes/projects.py:262-269`.
- Service: `backend/app/domain/projects/bom_import.py::commit`.

## BOM import presets (`/api/bom-presets`)

Reusable column-mapping configs for the BOM import wizard.

### `GET /api/bom-presets`

List active (non-archived) presets.

**Query** — `limit` (default 200, max 1000).

**Response** — `200 OK` — array of:

```json
{ "id": "…", "name": "…", "config": { … }, "created_at": "…", "updated_at": "…" }
```

`config` is the parsed JSON from `config_json` (`bom_presets.py:18-25`).

**Notes**

- Source: `backend/app/api/routes/bom_presets.py:28-43`.

### `POST /api/bom-presets`

Create a preset.

**Request** — `PresetIn`: `name`, `config` (free-form dict, JSON-serialised into `config_json`).

**Response** — `201 Created` — serialised preset.

**Notes**

- Source: `backend/app/api/routes/bom_presets.py:46-57`.

### `GET /api/bom-presets/{preset_id}`

Fetch one. `404 bom_preset.not_found` on miss.

**Notes**

- Source: `backend/app/api/routes/bom_presets.py:67-69`.

### `PATCH /api/bom-presets/{preset_id}`

Partial update. `name`, `config` are the editable fields; `config` is re-`json.dumps`'d into the column.

**Notes**

- Source: `backend/app/api/routes/bom_presets.py:72-80`.

### `DELETE /api/bom-presets/{preset_id}`

Hard-delete (`db.delete`).

**Notes**

- Source: `backend/app/api/routes/bom_presets.py:83-87`.

## TODOs

- TODO(verify): `BomEntryIn`/`BomEntryPatch` — exhaustive field list, `entry_type` allowed values (`domain/projects/schemas.py`).
- TODO(verify): `BomImportPreviewIn`/`BomImportCommitIn` shape and the preview→commit handoff (`domain/projects/bom_import.py`, `domain/projects/schemas.py`).
- TODO(verify): `PresetIn`/`PresetPatch` exact shape and whether `config` is validated server-side (`domain/projects/schemas.py`).
