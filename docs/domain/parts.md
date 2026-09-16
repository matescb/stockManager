# Parts

Audience: engineer

The `parts` table is the catalogue: one row per distinct component the workspace tracks. This page covers `part_type`, MPN uniqueness, the linked-provider lifecycle, and the archive contract.

For the model definition see [`data-model.md`](data-model.md#parts). For the MPN uniqueness rationale see [ADR-0004](../adr/0004-mpn-uniqueness-per-workspace.md).

## `part_type`

`Part.part_type: String(20)` (`backend/app/domain/parts/models.py:66-67`). Default `"local"`. Four values:

| Value | Meaning | Stock semantics |
|---|---|---|
| `local` | No upstream provider linkage. | Stock writes go straight to the part. |
| `linked` | A primary provider (Mouser / DigiKey) owns the canonical fields. `linked_provider`, `linked_external_id`, `last_refresh_at`, `description_locally_edited` are populated. | Same as `local` — the linkage is metadata for refresh/asset fetch, not a stock dimension. |
| `meta` | Aggregator: a "type-of" container whose members are real parts. Built from `part_meta_members`. | Holds **no** on-hand stock itself. BOM consumption against a meta-part picks from any registered member (`backend/app/domain/builds/service.py:46-56`). |
| `sub_assembly` | Output of a build. Created automatically when a project's `associated_subassembly_part_id` is set; the `build_produce` row stocks it. | Treated as a regular part for stock-read purposes. |

The vocabulary is enforced only at the call site (the create-part schema). There is no DB CHECK constraint.

`local` and `linked` are **derived**, not user-owned: they are a reading of `linked_provider`, re-synced by `sync_part_type_and_log` (`backend/app/domain/parts/part_type.py`) at both transitions — the primary branch of the refresh route (`backend/app/api/routes/parts_refresh.py:310-325`) and the unlink PATCH (`backend/app/api/routes/parts_core.py:389-392`). Each transition writes one `part.type_synced` audit row commented `part_type: local→linked` — a separate action from `part.updated` so the unlink PATCH, which writes both, never puts two rows under one action. `meta` and `sub_assembly` are user-declared roles and are never rewritten by a link event, even on a part that carries `linked_provider`. Alembic `0080` backfilled the 160 prod rows that drifted before this existed.

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

## Specs and category on a provider payload

A provider lookup result becomes `custom_fields` rows through exactly one
writer, `backend/app/domain/parts/services/spec_reconcile.py`, used by the
create path (`services/provider_import.py`) and the refresh route
(`api/routes/parts_refresh.py`) alike. Before that there were two copies of
"write the specs" and neither normalised anything, which is why prod carries
9,377 provider rows including 277 ECCN codes and about a thousand whose value
is literally `-`.

`spec_schema.normalise` sorts every upstream key into one of four buckets:

| Bucket | Key written | Notes |
|---|---|---|
| canonical | the schema key (`resistance`) | Un-namespaced from BOTH tiers, parsed into a display string plus a `value_num` sidecar, and stamped with `custom_fields.provider`. |
| catalog | the upstream key, namespaced per ADR-0031 | Price / stock / packaging. Unchanged by A3 — `web/src/lib/providerCatalog.ts` keys off these exact names. |
| optional | the upstream key, namespaced per ADR-0031 | Anything else parametric, kept verbatim, so ICs and connectors lose nothing. |
| dropped | nothing | Customs codes, `-` values, and aliases a higher-precedence alias already answered. |

Two rules follow from canonical keys being shared:

- **Precedence, not recency, decides a contested key.** `digikey` >
  `mouser` > an unranked adapter; a NULL `provider` is unclaimed.
  DigiKey's `Parameters[]` is a real attribute table, while Mouser's
  value for the same key is mined out of prose by nine regexes in
  `providers/mouser.py`.
- **Ownership for the delete pass is per row, not per key prefix.**
  `provider_fields.py::provider_wrote_custom_field_row` reads
  `custom_fields.provider` for a canonical key — strictly, so an unstamped
  row is nobody's — and falls back to the ADR-0031 namespace rule for
  everything else, so unlinking a demoted primary cannot take the part's
  bare `image_url` with it. Writing is looser: `provider_outranks` treats
  an unstamped row, and an archived one, as claimable.

Junk rows already on a part are **archived** rather than deleted, and the
read paths (`GET /api/custom-fields/by-object/...`, the MCP part-detail tool)
filter `archived_at IS NULL`. A stale-but-real row keeps the hard delete it
has always had.

`parts.category_id` is filled on the same pass when it is NULL, from the
provider's own taxonomy (`spec_schema.category_for_provider` →
`categories/service.py::resolve_category_path_or_root`). Nothing is created:
an unresolvable path comes back as `category_suggestion` on the response. A
category the user picked is never overruled — which is also why the part
keeps its own category's spec schema.

See [ADR-0034](../adr/0034-spec-schema.md) and
[ADR-0031](../adr/0031-primary-and-secondary-parts-providers.md).

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

`part_cad_keys` (`backend/app/domain/parts/models.py:91`) — secondary CAD-footprint identifiers per part. `source` distinguishes manual vs imported. The table carries `workspace_id` for direct isolation filtering, and migration `0054` adds a trigger so `workspace_id` must match the owning `part_id`. CASCADE on `workspace_id` and `part_id`. See [workspace-isolation](workspace-isolation.md).

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
| Create a linked part from a lookup | `domain/parts/services/provider_import.py::create_from_provider_lookup` | Returns `ProviderImportOutcome(part, category_suggestion)`. The reconcile counts are audited, not returned. |
| Write a provider payload onto a part | `domain/parts/services/spec_reconcile.py::reconcile_provider_specs` | The single writer for create AND refresh. |
| File an uncategorized part | `domain/parts/services/spec_reconcile.py::apply_provider_category` | Never overrules a category the user chose; creates nothing. |
| Build a configured provider | `domain/parts/providers/base.py::make_provider` | Factory keyed on `workspaces.parts_provider`. |
| Archive / restore / bulk-archive | `api/routes/parts_core.py::archive_part`, `unarchive_part`, `bulk_archive_parts` | Inline; no dedicated service. |
