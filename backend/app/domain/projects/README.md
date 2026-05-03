# projects

Audience: engineer

Owns `Project` (a BOM), `ProjectEntry` (BOM lines), `BomImportPreset`, and the CSV/XLSX BOM import wizard (`bom_import.py`).

## Files

| File | What |
|---|---|
| `models.py` | `Project`, `ProjectEntry`, `BomImportPreset` |
| `schemas.py` | Pydantic shapes for project / BOM CRUD + import wizard |
| `bom_import.py` | `preview` + `commit` for the BOM import wizard (CSV/XLSX → ProjectEntry rows) |

(No `service.py` — BOM CRUD is straightforward enough to live in routes; import logic is in `bom_import.py`.)

## Public surface

| Operation | Entry point |
|---|---|
| Preview a BOM file | `bom_import.py::preview` |
| Commit a previewed BOM | `bom_import.py::commit` |

Internals worth knowing: `_detect_encoding`, `_detect_separator`, `_apply_mapping`, `_match_part` (resolves a row to a `Part` by MPN / mfg+pn / local part name).

## Hard rules (this module)

1. **`ProjectEntry.part_id` is nullable** — a BOM can reference a part that doesn't exist yet (unmatched row). Builds must skip / surface unmatched entries explicitly.
2. **Workspace isolation on every lookup.** `_match_part` filters by `workspace_id`. See [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md).
3. **Row cap is enforced in `_enforce_row_cap`** — protects against pathological uploads.

## See also

- [Domain doc — builds & BOM](../../../../docs/domain/builds-and-bom.md) — how `ProjectEntry` flows into `shortage_analysis`
- [API — projects](../../../../docs/api/projects.md) — REST surface (projects + bom-presets + import wizard)

## Don't

- Don't infer part match outside `_match_part` — keep one match algorithm.
- Don't drop unmatched rows silently on commit; the route must report them.
- Don't lift the row cap without a corresponding API contract change — the wizard UI assumes it.
