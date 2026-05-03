# Phase 3 — Projects, BOM, and cross-cutting metadata

Audience: engineer

> Note: retro-documented 2026-05-03 from migration 0001; the original
> PR predates the phase-docs convention.

Adds projects (the BOM container), `project_entries` (the BOM rows),
the BOM-import preset slot, and the three cross-cutting tables —
`attachments`, `custom_fields`, `tags` / `tag_links` — that hang off
any domain object via a `(object_type, object_id)` polymorphic key.

## Why

- A BOM is the join between "what we want to build" (projects) and
  "what we have" (parts + stock). It had to be a first-class table so
  the [Phase 5](05-builds.md) build engine could consume it directly.
- File uploads, free-form key/value fields, and tags are the same
  problem repeated across parts, projects, orders, lots, and storage
  locations. Modelling them once with a polymorphic FK avoided
  growing N parallel sets of join tables.
- The import wizard needed a place to persist column-mapping
  configs so users wouldn't redo them every time —
  `bom_import_presets` was scaffolded here, wired up in
  [Phase 7](07-bom-presets.md).

## What shipped

- `projects` — `name`, `description`, `notes_markdown`,
  `associated_subassembly_part_id` (deferred FK, breaks the
  parts↔projects cycle). Source:
  `backend/alembic/versions/0001_initial.py:116-137` and the
  use-alter constraint at `:382-388`.
- `project_entries` — the BOM line. `entry_type ∈ {part, meta_part,
  free_text}`, `part_id?`, `meta_part_id?`, `quantity` (Numeric here;
  later tightened to Integer in `0032_integer_quantities.py`),
  `designators` (Postgres ARRAY), `cad_footprint`, `cad_key`, `dnp`,
  `order_index`. Source: `0001_initial.py:317-348`.
- `bom_import_presets` — workspace-scoped name + opaque
  `config_json` (Text). Schema-less by design so the wizard can
  evolve its mapping format without a migration. Source:
  `0001_initial.py:78-94`. CRUD wired up in
  [Phase 7](07-bom-presets.md).
- `attachments` — polymorphic file metadata: `(object_type,
  object_id)`, `file_name`, `file_type`, `mime_type`, `size_bytes`,
  `storage_key`, `uploaded_by`. Source: `0001_initial.py:54-77`.
- `custom_fields` — polymorphic `(object_type, object_id, key, value)`
  with `uq_cf_unique` on `(workspace_id, object_type, object_id,
  key)`. Source: `0001_initial.py:95-115`. Used by the provider
  catalog (price/stock/manufacturer URL) in
  [Phase 11](11-providers-and-scan.md).
- `tags` + `tag_links` — workspace-unique tag names; `tag_links` is
  the polymorphic many-to-many to any domain object. Source:
  `0001_initial.py:160-177` and `:229-249`.

## Invariants introduced

- **Polymorphic tables key on `(workspace_id, object_type,
  object_id)`.** Every read filters by `workspace_id` first; the
  shared index `ix_<table>_object` covers the lookup. See
  `CLAUDE.md` and the workspace-isolation ADR (`../adr/`).
- **Custom-field keys split into "catalog" vs "spec".** The split is
  defined client-side in `web/src/lib/providerCatalog.ts` and
  mirrored server-side in
  `backend/app/domain/parts/services/provider.py`. Adding a new
  catalog key needs both sides. (Convention introduced retroactively
  in [Phase 11](11-providers-and-scan.md); the table itself is
  Phase 3.)
- **The `projects → parts` FK is deferred** (`use_alter=True`) — the
  cycle is `parts.project_id → projects.id` and
  `projects.associated_subassembly_part_id → parts.id`. Both columns
  are `ON DELETE SET NULL`.

## Things deferred

- BOM-import preset CRUD + UI — [Phase 7](07-bom-presets.md).
- Attachment XSS / MIME hardening (allow-list, magic-byte sniff,
  filename sanitiser, size cap) — security batch PR #4 (see
  `CHANGELOG.md` "Tier C / D — concrete CRITs").
- Polymorphic-orphan cleanup indexes —
  `0033_polymorphic_orphan_indexes.py`.
- Trigram search across the catalogue —
  `0031_search_pg_trgm_indexes.py`.
- `CustomFieldIn.source` removal (was client-controllable; would let
  callers forge `source='provider'` rows) — security batch PR #1/#2.

## References

- Migration: `backend/alembic/versions/0001_initial.py`
- Tables created here: `projects`, `project_entries`,
  `bom_import_presets`, `attachments`, `custom_fields`, `tags`,
  `tag_links`.
- Architecture: `docs/ARCHITECTURE.md` — polymorphic tables and the
  `WorkspaceOwned` mixin.
- Related phases: [Phase 7](07-bom-presets.md) (preset wiring),
  [Phase 11](11-providers-and-scan.md) (custom-field catalog use).
