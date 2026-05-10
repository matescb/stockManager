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

**Request** — `BomImportPreviewIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `text_b64` | string | yes | Base64 CSV/TSV/plain text payload. Runtime decoded cap is 4 MB. |
| `separator` | string \| null | no | Auto-detected when omitted. |
| `encoding` | string \| null | no | Auto-detected when omitted. |
| `has_header` | bool \| null | no | Auto-detected when omitted. |
| `auto_create_missing_parts` | bool | no | Default `false`. When `true`, preview reports auto-create and skip counters. |
| `mapping` | `BomMappingField[]` \| null | no | Required for meaningful auto-create counters because MPN/name columns must be known. |
| `designator_separator` | string | no | Default `,`. Used when `mapping` contains `designators`. |

**Response** — `200 OK` — envelope: `{ data, status }`

```json
{
  "detected_separator": ",",
  "detected_encoding": "utf-8",
  "has_header": true,
  "headers": ["qty", "mpn"],
  "rows": [{ "cells": ["1", "NEW-MPN"] }],
  "would_auto_create_count": 1,
  "would_skip_count": 0
}
```

**Notes**

- Source: `backend/app/api/routes/projects.py:256-259`.
- Service: `backend/app/domain/projects/bom_import.py::preview`.

### `POST /api/projects/{project_id}/bom/import`

Commit the preview into `project_entries` rows.

**Request** — `BomImportCommitIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `text_b64` | string | yes | Same base64 payload used for preview. |
| `separator` | string | yes | Delimiter to parse with. |
| `encoding` | string | yes | Text encoding to decode with. |
| `has_header` | bool | yes | Whether to skip the first row. |
| `mapping` | `BomMappingField[]` | yes | Column-to-field mapping. Targets include `quantity`, `part`, `mpn`, `manufacturer`, `internal_part_number`, `designators`, `comments`, `footprint`, `id_code`, `cad_key`, `dnp`, `ignore`. |
| `designator_separator` | string | no | Default `,`. |
| `auto_create_missing_parts` | bool | no | Default `false`. When `true`, rows that do not match but have MPN or part/name create a zero-stock `Part`. Rows with neither MPN nor part/name are skipped. |

**Response** — `200 OK` — envelope: `{ data, status }`

```json
{
  "inserted": 1,
  "matched": 0,
  "unmatched": 0,
  "auto_created": 1,
  "skipped": 0
}
```

`auto_created` counts new `Part` rows, not repeated BOM rows that merge onto a part created earlier in the same import.

**Notes**

- The dep rolls back on any raise, so no explicit try/except is needed (`projects.py:265-267`).
- Auto-create preserves the import match priority in `bom_import.py::_match_part`; creation happens only after no candidate is found.
- Created parts use the import workspace, have no linked provider, no default storage location, and no stock entries or lots.
- Source: `backend/app/api/routes/projects.py:262-269`.
- Service: `backend/app/domain/projects/bom_import.py::commit`.

### `POST /api/projects/{project_id}/bom/import-from-provider`

Create real provider-backed parts for unmatched BOM rows, then bind each
successful row to the new part. The route is workspace-scoped, member-access,
and rate-limited at 30 calls/minute per workspace.

**Request** — `BomProviderImportIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `entry_ids` | UUID[] \| null | no | `null` imports all unmatched rows in the project. A list imports only those rows. |

**Response** — `200 OK` — envelope: `{ data, status }`

```json
{
  "created": 1,
  "pending_choices": [],
  "failures": [{ "entry_id": "…", "mpn": "NOPE", "reason": "no match" }],
  "provider": "mouser"
}
```

**Errors** — `404` for a foreign/missing project. `409 bom_provider.no_provider`
when the workspace has no configured parts provider.

**Notes**

- Uses per-row savepoints; one failed lookup or write does not roll back rows that already created parts.
- This is not the CSV `auto_create_missing_parts` path. It does not create stub parts; failures stay unmatched.
- Source: `backend/app/api/routes/projects.py:276-294`.
- Service: `backend/app/domain/projects/bom_import_provider.py:21-72`.

### `POST /api/projects/{project_id}/bom/import-from-provider/commit-choices`

Commit manufacturer choices returned by `pending_choices`.

**Request** — `BomProviderImportChoiceIn`

| Field | Type | Required | Notes |
|---|---|---|---|
| `choices` | object | yes | Map of `entry_id` to chosen manufacturer. |

**Response** — `200 OK` — same shape as `import-from-provider`.

**Errors** — same project/provider errors as `import-from-provider`.

**Notes**

- Re-runs provider lookup for each selected entry and creates the part only when the selected manufacturer is present.
- Source: `backend/app/api/routes/projects.py:297-315`.
- Service: `backend/app/domain/projects/bom_import_provider.py:75-130`.

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
- TODO(verify): `PresetIn`/`PresetPatch` exact shape and whether `config` is validated server-side (`domain/projects/schemas.py`).
