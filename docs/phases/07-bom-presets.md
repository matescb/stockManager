# Phase 7 — BOM import presets

Audience: engineer

The `bom_import_presets` table existed since Phase 1 but was unused.
Phase 7 wires it into the import wizard so users can save / recall the
column-mapping config they normally use for a CAD tool's BOM export.

## Endpoints

```
GET    /api/bom-presets        list (workspace-scoped)
POST   /api/bom-presets        { name, config }
GET    /api/bom-presets/{id}
PATCH  /api/bom-presets/{id}   { name?, config? }
DELETE /api/bom-presets/{id}
```

`config` is an opaque JSON object; the import wizard stores:

```json
{
  "separator": ";",
  "encoding": "utf-8",
  "has_header": true,
  "designator_separator": ",",
  "mapping": [
    { "column_index": 0, "target": "quantity" },
    { "column_index": 1, "target": "mpn" }
  ]
}
```

The backend doesn't validate `config` schema — it's whatever the
wizard last wrote. This keeps presets future-compatible if new mapping
targets are added.

## UI

In `/projects/{id}/import` step 2, the toolbar gains:

- **Load preset** dropdown — applies separator, encoding, header
  flag, designator separator, and full mapping array to the current
  state.
- **Save preset** button — prompts for a name, posts the current
  state as a new preset.
- **Manage** dropdown — list with per-preset delete.

## Tests

`backend/tests/test_bom_presets.py`: full CRUD and per-workspace
isolation.
