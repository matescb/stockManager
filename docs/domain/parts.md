# Parts

Audience: engineer

The `parts` table is the catalogue: one row per distinct component the workspace tracks. This page covers `part_type`, MPN uniqueness, the linked-provider lifecycle, and the archive contract.

For the model definition see [`data-model.md`](data-model.md#parts). For the MPN uniqueness rationale see [ADR-0004](../adr/0004-mpn-uniqueness-per-workspace.md).

## `part_type`

`Part.part_type: String(20)` (`backend/app/domain/parts/models.py:59`). Default `"local"`. Four values:

| Value | Meaning | Stock semantics |
|---|---|---|
| `local` | Manually-entered part. No upstream provider linkage. | Stock writes go straight to the part. |
| `linked` | Created via provider lookup (Mouser / DigiKey). `linked_provider`, `linked_external_id`, `last_refresh_at`, `description_locally_edited` are populated. | Same as `local` — the linkage is metadata for refresh/asset fetch, not a stock dimension. |
| `meta` | Aggregator: a "type-of" container whose members are real parts. Built from `part_meta_members`. | Holds **no** on-hand stock itself. BOM consumption against a meta-part picks from any registered member (`backend/app/domain/builds/service.py:46-56`). |
| `sub_assembly` | Output of a build. Created automatically when a project's `associated_subassembly_part_id` is set; the `build_produce` row stocks it. | Treated as a regular part for stock-read purposes. |

The vocabulary is enforced only at the call site (the create-part schema). There is no DB CHECK constraint.

## MPN uniqueness

The load-bearing rule. Partial unique index `uq_parts_ws_mpn` on `(workspace_id, mpn)` with predicate `mpn IS NOT NULL AND archived_at IS NULL` (`backend/app/domain/parts/models.py:33-39`, alembic 0011).

Implications:

- Two active parts in the same workspace cannot share an MPN.
- `mpn IS NULL` is allowed any number of times — manual / sub-assembly parts often have no MPN.
- **Archiving frees the MPN.** An archived part is excluded from the index, so a replacement can claim the same MPN. This is intentional — archive is the unmake operation. See [ADR-0004](../adr/0004-mpn-uniqueness-per-workspace.md).
- Two workspaces can have the same MPN — the index is `(workspace_id, mpn)`.

The create-part route returns `409 Conflict` with `{ existing_id, existing_name }` on collision (`CLAUDE.md` — "Hard invariants").

## Indexes

`Part.__table_args__` (`backend/app/domain/parts/models.py:27-57`):

- `ix_parts_ws_name` — sort/filter listings.
- `uq_parts_ws_mpn` — partial unique, predicate above.
- `ix_parts_ws_ipn` — internal part number lookup.
- `ix_parts_ws_archived` — universal active-row filter.
- `ix_parts_ws_name_trgm`, `ix_parts_ws_mpn_trgm` — pg_trgm GIN for ILIKE search (alembic 0018, BE2-018). Single-column GIN; the planner bitmap-ANDs with the (workspace_id, archived_at) btree.

## Provider linkage (`linked` parts)

Three fields cooperate (`backend/app/domain/parts/models.py:78-88`):

| Field | Role |
|---|---|
| `linked_provider` | Which provider owns the canonical fields (`mouser` / `digikey`). |
| `linked_external_id` | Upstream identifier (e.g. Mouser's `ManufacturerPartNumber` after lookup). |
| `last_refresh_at` | Updated on every successful provider fetch. |
| `description_locally_edited` | Flips to `True` when a user edits the description on a linked part, so subsequent refreshes don't overwrite it. |

The provider lookup pipeline lives in `backend/app/domain/parts/providers/`; see [providers](providers.md).

## Default storage

Two columns govern where stock for a part lands by default:

- `default_storage_location_id` (`backend/app/domain/parts/models.py:72-74`) — FK to `storage_locations`, `ON DELETE SET NULL`. **DB-enforced workspace check** via `parts_default_storage_workspace_check` trigger (alembic 0036) — see [workspace-isolation](workspace-isolation.md).
- `default_storage_mandatory: Boolean` (`backend/app/domain/parts/models.py:75`) — when true, `add_stock` rejects any write whose storage either omits or differs from `default_storage_location_id` (`backend/app/domain/stock/service.py:418-426`).

The mandatory check covers the omitted-storage case explicitly — earlier the chain short-circuited when `storage` was None and any row that simply omitted `storage_location_id` would land with NULL even on a mandatory-default part. The bulk-import-from-scan flow exploited this implicitly. Fixed in BE CRIT-2.

## Serialization

`serialized: Boolean` (`backend/app/domain/parts/models.py:76`). When the workspace has `serial_tracking_enabled` AND the part is `serialized`, every stock-add must produce exactly one serialised lot (quantity=1, `lot.serial_number` required). Enforced in `add_stock` (`backend/app/domain/stock/service.py:431-436`) and `orders.receive` (`backend/app/domain/orders/service.py:106-114`).

See [lots-and-serials](lots-and-serials.md).

## Archive contract

`archived_at: DateTime` (inherited from `WorkspaceOwned`). Soft-archive is the universal delete pattern.

- The route is `POST /api/parts/{id}/archive` (`backend/app/api/routes/parts_core.py:297`).
- Read endpoints can opt in with `?archived=true` (`backend/app/api/routes/parts_core.py:60`).
- The "load even if archived" path uses `_get_part(..., include_archived=True)` so the archived part page still loads — but write endpoints reject archived parts (`backend/app/api/routes/parts_core.py:212-223`).
- Bulk archive via `POST /api/parts/bulk-archive` returns `{ archived_ids, already_archived_ids, not_found_ids }` (`backend/app/api/routes/parts_core.py:379-440`).
- Archiving frees the MPN for re-use (the partial unique excludes `archived_at IS NOT NULL` rows).

Hard-delete is not exposed; FKs use `SET NULL` so a hypothetical hard-delete would leave the audit trail intact.

## Adjacent tables

`part_cad_keys` (`backend/app/domain/parts/models.py:91`) — secondary CAD-footprint identifiers per part. `source` distinguishes manual vs imported. CASCADE on `part_id`.

`part_meta_members` (`backend/app/domain/parts/models.py:100`) — a meta-part's registered concrete members. Composite unique `(meta_part_id, part_id)`. CASCADE on either side. See [builds-and-bom](builds-and-bom.md) for how members are used during consume.

`part_substitutes` (`backend/app/domain/parts/models.py:111`) — registered substitute relationships between regular parts. `direction` is `bidirectional` (default) or one-way. CASCADE on either side. The build-consume path expands the candidate set via `_candidate_part_ids` (`backend/app/domain/builds/service.py:46-74`).

`bulk_import_idempotency` lives in this module too — covered in [scan-import](scan-import.md).

## Service entry points

There is no dedicated `parts/service.py`. Logic for parts splits across the route module and the helpers under `backend/app/domain/parts/services/`:

| Operation | Entry point | Notes |
|---|---|---|
| Compute bag signature | `domain/parts/services/bag_signature.py::compute_bag_signature` | Server-side mirror of TS `bagSignature`; see [scan-import](scan-import.md). |
| Provider MPN lookup with cache | `domain/parts/services/provider_cache.py::lookup_with_cache` | TTL cache + per-provider circuit breaker. |
| Force-fresh provider lookup | `domain/parts/services/provider_cache.py::lookup_fresh` | Skips cache read; still applies circuit breaker. |
| Download provider asset | `domain/parts/services/assets.py::fetch_provider_asset` | SSRF-hardened download to UPLOAD_DIR. |
| Build a configured provider | `domain/parts/providers/base.py::make_provider` | Factory keyed on `workspaces.parts_provider`. |
| Archive / restore / bulk-archive | `api/routes/parts_core.py::archive_part`, `unarchive_part`, `bulk_archive_parts` | Inline; no dedicated service. |
