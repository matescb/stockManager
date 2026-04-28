# Phase 5 — Builds & consume-from-BOM

A "build" runs a project's BOM against actual on-hand stock, consuming
parts and (optionally) producing an output sub-assembly lot.

## Domain

`builds` table (per workspace):

| Field | Notes |
|---|---|
| `name` | required, e.g. `BUILD-2026-001` |
| `project_id` | FK projects, required |
| `quantity` | how many copies of the project to build (>0) |
| `status` | `planned` → `in_progress` → `complete`, plus `cancelled` |
| `started_at`, `completed_at` | timestamps |
| `output_lot_id` | the resulting sub-assembly lot, if the project has `associated_subassembly_part_id` |
| `comments` | free text |

`stock_entries.build_id` and `lots.source_build_id` (already in the
schema since Phase 1) are now populated whenever a build executes.

## Required-quantity formula

For each consumable BOM entry (`entry_type ∈ {part, meta_part}`,
`dnp = false`, `part_id` set), required quantity for the whole build is:

```
required = ceil(entry.quantity * build.quantity * (1 + part.attrition_percentage/100))
required = max(required, entry.quantity * build.quantity + part.attrition_min_quantity)
```

(The `attrition_min_quantity` floor ensures at least N spares are
consumed even when the percentage rounds to zero.)

## Endpoints

```
GET    /api/builds                 list (filter by project_id, archived)
POST   /api/builds                 create planned build
GET    /api/builds/{id}            { build, shortage[] }   ← per-entry analysis
PATCH  /api/builds/{id}            edit metadata + status (locked once complete)
POST   /api/builds/{id}/archive
POST   /api/builds/{id}/restore
POST   /api/builds/{id}/consume    execute (atomic)
```

`GET /api/builds/{id}` includes a `shortage[]` array — for each
consumable BOM entry: `required`, `available` (on-hand of main part),
`substitute_ids[]`, `substitute_available` (Σ on-hand of registered
substitutes — both directions for `bidirectional`, otherwise main→sub
only), `short_by` (`max(0, required − available − substitute_available)`).

## Consume request

```json
{
  "lines": [
    { "project_entry_id": "…", "part_id": "<main or sub>", "quantity": 5,
      "lot_id": "…optional…", "storage_location_id": "…optional…" }
  ],
  "output_storage_location_id": "…optional…",
  "output_lot_name": "BUILD-2026-001-out"
}
```

Validation (all-or-nothing per request):

1. Each line's `part_id` is the entry's main part **or** a registered
   substitute (one-way `main → sub`, or bidirectional in either dir).
2. The chosen part has enough stock at the chosen lot/storage filter.
3. After processing all lines, **every** consumable entry must be fully
   covered to its `required` quantity (sum across all lines for that
   entry). Under-consumption is rejected.

On success, for each line:
- One `stock_entries` row with `operation_type='build_consume'`,
  negative `quantity_delta`, `build_id` and `project_id` set.

If `project.associated_subassembly_part_id` is set, the service also
creates:
- A `lots` row with `source_type='build'`, `source_build_id=build.id`,
  `purchase_quantity=build.quantity`.
- A `stock_entries` row with `operation_type='build_produce'`, positive
  `quantity_delta=build.quantity`, that lot id, and the chosen output
  storage location.

The build is then marked `status='complete'` and is read-only (apart
from cancelling/archiving) — re-consume requests are rejected.

## UI

- `/builds` list with status badge.
- `/builds/create?project_id=…` form (project pre-selected when arriving
  from the project detail).
- `/builds/{id}` — shortage table + per-entry consumption plan editor;
  **Auto-fill** suggests "drain main, fall back to first substitute".
- `/projects/{id}/builds` lists builds against that project.

## Tests

`backend/tests/test_builds.py`:

- shortage analysis numbers (ceil + attrition + bidirectional substitutes)
- happy-path consume that decrements stock
- consumed builds are read-only (re-consume rejected)
- under-consume rejection
- DNP entries are skipped from both shortage and required-coverage
- substitute consumption accepted
- non-substitute part rejected with clear error

20 tests pass.
