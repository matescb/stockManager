# Data Model

Audience: engineer

The full entity catalogue: every model, every FK, the polymorphic surfaces. This is the most-referenced page under `docs/domain/`.

For the one-line domain table (which router serves which tables), see [`ARCHITECTURE.md` — "Domain decomposition"](../ARCHITECTURE.md#domain-decomposition); this page is the deeper version.

## Conventions

- Every workspace-scoped table inherits `WorkspaceOwned` (`backend/app/domain/_mixins.py:11-21`): `id` (UUID PK), `workspace_id` (FK, `ON DELETE CASCADE`), `created_at`, `updated_at`, `created_by`/`updated_by` (FK to `users`, `SET NULL`), `archived_at`.
- Soft-archive is the universal delete pattern: `archived_at IS NOT NULL` rows are excluded from active queries via partial indexes (`postgresql_where=text("archived_at IS NULL")`). Hard-delete is rare.
- Cross-table FKs use `ON DELETE SET NULL` so a hard-delete of a parent (e.g. an order) preserves audit trail in `stock_entries.order_id`. The exceptions are intra-domain FKs (`order_entries.order_id`, `part_meta_members.meta_part_id`, etc.) which use `CASCADE`.
- Every model module is registered in `backend/app/domain/all_models.py`. The test `tests/test_migrations.py::test_all_models_covers_every_domain` walks `app/domain/*/models.py` and asserts each `__tablename__` is in `Base.metadata.tables`.
- Polymorphic tables (`attachments`, `custom_fields`, `tag_links`) reference parents via `(object_type, object_id)` with **no FK on `object_id`**. See [polymorphic](polymorphic.md).

## Entity catalogue (28 models)

### users + workspaces

| Model | Table | Source |
|---|---|---|
| `User` | `users` | `backend/app/domain/users/models.py:12` |
| `UserSession` | `user_sessions` | `backend/app/domain/users/models.py:25` |
| `UserLoginFailure` | `user_login_failures` | `backend/app/domain/users/models.py:49` |
| `PendingUser` | `pending_users` | `backend/app/domain/users/models.py:84` |
| `Workspace` | `workspaces` | `backend/app/domain/workspaces/models.py:13` |
| `WorkspaceMember` | `workspace_members` | `backend/app/domain/workspaces/models.py:55` |
| `WorkspaceInvitation` | `workspace_invitations` | `backend/app/domain/workspaces/models.py:69` |
| `WorkspaceCatalogToken` | `workspace_catalog_tokens` | `backend/app/domain/workspaces/models.py:107` |

`User` is *not* workspace-scoped (precedes workspaces). `PendingUser` is intentionally not workspace-scoped (signup precedes workspace creation, see model docstring `backend/app/domain/users/models.py:91-95`). `UserLoginFailure` is also unscoped — login is pre-workspace.

### parts

| Model | Table | Source |
|---|---|---|
| `Part` | `parts` | `backend/app/domain/parts/models.py:25` |
| `PartCadKey` | `part_cad_keys` | `backend/app/domain/parts/models.py:91` |
| `PartMetaMember` | `part_meta_members` | `backend/app/domain/parts/models.py:100` |
| `PartSubstitute` | `part_substitutes` | `backend/app/domain/parts/models.py:111` |
| `BulkImportIdempotency` | `bulk_import_idempotency` | `backend/app/domain/parts/models.py:123` |

`Part` carries `part_type` (`linked|local|meta|sub_assembly`) — see [parts](parts.md). The MPN partial unique `uq_parts_ws_mpn` is the load-bearing constraint (`backend/app/domain/parts/models.py:33-39`).

### storage

| Model | Table | Source |
|---|---|---|
| `StorageLocation` | `storage_locations` | `backend/app/domain/storage/models.py:9` |

`single_part_only` and `existing_parts_only` flags are application-enforced inside `enforce_storage_constraints` under a per-(workspace, storage) advisory lock — there is no DB CHECK (`backend/app/domain/stock/service.py:330-391`).

### stock + lots

| Model | Table | Source |
|---|---|---|
| `StockEntry` | `stock_entries` | `backend/app/domain/stock/models.py:22` |
| `Lot` | `lots` | `backend/app/domain/lots/models.py:10` |

`StockEntry` is append-only — see [ledger](ledger.md). `Lot.parent_lot_id` self-references for split lineage; see [lots-and-serials](lots-and-serials.md).

### orders

| Model | Table | Source |
|---|---|---|
| `Order` | `orders` | `backend/app/domain/orders/models.py:20` |
| `OrderEntry` | `order_entries` | `backend/app/domain/orders/models.py:45` |

### projects

| Model | Table | Source |
|---|---|---|
| `Project` | `projects` | `backend/app/domain/projects/models.py:21` |
| `ProjectEntry` | `project_entries` | `backend/app/domain/projects/models.py:50` |
| `BomImportPreset` | `bom_import_presets` | `backend/app/domain/projects/models.py:82` |

`Project.associated_subassembly_part_id` references `parts` via `use_alter=True` to break the `parts ↔ projects` cycle at create time (`backend/app/domain/projects/models.py:38-47`).

### builds

| Model | Table | Source |
|---|---|---|
| `Build` | `builds` | `backend/app/domain/builds/models.py:18` |

### cross-cutting (polymorphic)

| Model | Table | Source |
|---|---|---|
| `Attachment` | `attachments` | `backend/app/domain/attachments/models.py:10` |
| `CustomField` | `custom_fields` | `backend/app/domain/custom_fields/models.py:10` |
| `Tag` | `tags` | `backend/app/domain/tags/models.py:10` |
| `TagLink` | `tag_links` | `backend/app/domain/tags/models.py:28` |

### audit

| Model | Table | Source |
|---|---|---|
| `AuditLog` | `audit_log` | `backend/app/domain/audit/models.py:13` |

`target_ids` is `UUID[]` with a GIN index (added in alembic 0030 — TODO(verify): the model comment says "0024" but the file is `0030_audit_log.py` — `backend/app/domain/audit/models.py:30`).

## ER diagram

Models are grouped by domain. Solid edges are real FKs; dotted edges are the polymorphic `(object_type, object_id)` references that have **no** `object_id` FK.

```mermaid
erDiagram
  USERS ||--o{ USER_SESSIONS : "user_id"
  USERS ||--o{ USER_LOGIN_FAILURES : "user_id (SET NULL)"
  USERS ||--o{ WORKSPACES : "owner_user_id (RESTRICT)"
  USERS ||--o{ WORKSPACE_MEMBERS : "user_id"
  USERS ||--o{ WORKSPACE_INVITATIONS : "invited_by/accepted_by"
  USERS ||--o{ WORKSPACE_CATALOG_TOKENS : "created_by_user_id"

  WORKSPACES ||--o{ WORKSPACE_MEMBERS : ""
  WORKSPACES ||--o{ WORKSPACE_INVITATIONS : ""
  WORKSPACES ||--o{ WORKSPACE_CATALOG_TOKENS : ""
  WORKSPACES ||--o{ AUDIT_LOG : ""
  WORKSPACES ||--o{ PARTS : ""
  WORKSPACES ||--o{ STORAGE_LOCATIONS : ""
  WORKSPACES ||--o{ STOCK_ENTRIES : ""
  WORKSPACES ||--o{ LOTS : ""
  WORKSPACES ||--o{ ORDERS : ""
  WORKSPACES ||--o{ ORDER_ENTRIES : ""
  WORKSPACES ||--o{ PROJECTS : ""
  WORKSPACES ||--o{ PROJECT_ENTRIES : ""
  WORKSPACES ||--o{ BOM_IMPORT_PRESETS : ""
  WORKSPACES ||--o{ BUILDS : ""
  WORKSPACES ||--o{ ATTACHMENTS : ""
  WORKSPACES ||--o{ CUSTOM_FIELDS : ""
  WORKSPACES ||--o{ TAGS : ""
  WORKSPACES ||--o{ TAG_LINKS : ""
  WORKSPACES ||--o{ BULK_IMPORT_IDEMPOTENCY : ""

  PARTS ||--o{ PART_CAD_KEYS : "part_id"
  PARTS ||--o{ PART_META_MEMBERS : "meta_part_id / part_id"
  PARTS ||--o{ PART_SUBSTITUTES : "part_id / substitute_part_id"
  PARTS ||--o{ STOCK_ENTRIES : "part_id"
  PARTS ||--o{ LOTS : "part_id"
  PARTS ||--o{ ORDER_ENTRIES : "part_id (SET NULL)"
  PARTS ||--o{ PROJECT_ENTRIES : "part_id / meta_part_id (SET NULL)"
  PARTS }o--o| STORAGE_LOCATIONS : "default_storage_location_id (SET NULL)"
  PARTS }o--o| PROJECTS : "project_id (SET NULL)"
  PROJECTS }o--o| PARTS : "associated_subassembly_part_id (SET NULL, use_alter)"

  STORAGE_LOCATIONS ||--o{ STOCK_ENTRIES : "storage_location_id (SET NULL)"
  LOTS ||--o{ STOCK_ENTRIES : "lot_id (SET NULL)"
  LOTS }o--o| LOTS : "parent_lot_id (SET NULL)"
  STOCK_ENTRIES }o--o| STOCK_ENTRIES : "related_entry_id (SET NULL)"

  ORDERS ||--o{ ORDER_ENTRIES : "order_id (CASCADE)"
  ORDERS ||--o{ STOCK_ENTRIES : "order_id (SET NULL)"
  ORDER_ENTRIES ||--o{ STOCK_ENTRIES : "order_entry_id (SET NULL)"
  ORDERS ||--o{ LOTS : "source_order_id (SET NULL)"

  PROJECTS ||--o{ PROJECT_ENTRIES : "project_id (CASCADE)"
  PROJECTS ||--o{ BUILDS : "project_id (CASCADE)"
  PROJECTS ||--o{ STOCK_ENTRIES : "project_id (SET NULL)"

  BUILDS ||--o{ STOCK_ENTRIES : "build_id (SET NULL)"
  BUILDS ||--o{ LOTS : "source_build_id (SET NULL)"
  BUILDS }o--o| LOTS : "output_lot_id (SET NULL)"

  TAGS ||--o{ TAG_LINKS : "tag_id (CASCADE)"

  ATTACHMENTS }..o{ PARTS : "object_id (no FK)"
  CUSTOM_FIELDS }..o{ PARTS : "object_id (no FK)"
  TAG_LINKS }..o{ PARTS : "object_id (no FK)"
```

The polymorphic dotted edges go to many parent types, not just `parts` — `object_type` discriminates among `part | order | project | build | lot | storage_location | …`. Mermaid can't draw "edge to N tables" cleanly; the diagram shows `parts` as the common case. Real `object_type` values are decided at the call site (e.g. `attachments` route handlers) and there is no enum on the column.

## FK summary table

The non-trivial cross-domain FKs and their delete behaviour. Within-domain CASCADE FKs (e.g. `tag_links.tag_id → tags.id`) are omitted.

| Source column | Target | On delete | Source |
|---|---|---|---|
| `parts.default_storage_location_id` | `storage_locations.id` | `SET NULL` (+ BEFORE trigger checks workspace) | `backend/app/domain/parts/models.py:72-74`, `backend/alembic/versions/0036_parts_default_storage_ws_trigger.py` |
| `parts.project_id` | `projects.id` | `SET NULL` | `backend/app/domain/parts/models.py:68` |
| `projects.associated_subassembly_part_id` | `parts.id` | `SET NULL` (`use_alter`) | `backend/app/domain/projects/models.py:38-47` |
| `stock_entries.lot_id` | `lots.id` | `SET NULL` | `backend/app/domain/stock/models.py:51` |
| `stock_entries.storage_location_id` | `storage_locations.id` | `SET NULL` | `backend/app/domain/stock/models.py:52-54` |
| `stock_entries.order_id` | `orders.id` | `SET NULL` (`fk_stock_entries_order_id`) | `backend/app/domain/stock/models.py:64-68` |
| `stock_entries.order_entry_id` | `order_entries.id` | `SET NULL` (`fk_stock_entries_order_entry_id`) | `backend/app/domain/stock/models.py:69-73` |
| `stock_entries.project_id` | `projects.id` | `SET NULL` | `backend/app/domain/stock/models.py:74` |
| `stock_entries.build_id` | `builds.id` | `SET NULL` (`fk_stock_entries_build_id`) | `backend/app/domain/stock/models.py:75-79` |
| `stock_entries.related_entry_id` | `stock_entries.id` | `SET NULL` (self-ref; circular, written under savepoint) | `backend/app/domain/stock/models.py:60` |
| `stock_entries.created_by` | `users.id` | `SET NULL` | `backend/app/domain/stock/models.py:86` |
| `lots.parent_lot_id` | `lots.id` | `SET NULL` (split lineage) | `backend/app/domain/lots/models.py:27` |
| `lots.source_order_id` | `orders.id` | `SET NULL` (`fk_lots_source_order_id`) | `backend/app/domain/lots/models.py:35-39` |
| `lots.source_build_id` | `builds.id` | `SET NULL` (`fk_lots_source_build_id`) | `backend/app/domain/lots/models.py:40-44` |
| `builds.output_lot_id` | `lots.id` | `SET NULL` | `backend/app/domain/builds/models.py:32` |
| `builds.project_id` | `projects.id` | `CASCADE` | `backend/app/domain/builds/models.py:27` |
| `workspaces.owner_user_id` | `users.id` | `RESTRICT` (deletion guarded by `users/service.py::assert_user_deletable`) | `backend/app/domain/workspaces/models.py:29` |
| `audit_log.user_id` | `users.id` | `SET NULL` | `backend/app/domain/audit/models.py:23-27` |

Constraint names are pinned for the cross-domain `SET NULL` FKs added in `0018_db_schema_cleanup.py` so that downgrade can drop them by name (see migration comments at `backend/app/domain/stock/models.py:61-63`).

## DB-level invariants

These are the only behaviours not enforceable purely in application code.

- **`ck_stock_nonneg` (alembic 0013).** AFTER INSERT trigger on `stock_entries`. Re-aggregates `SUM(quantity_delta)` for the matching `(workspace_id, part_id, lot_id, storage_location_id, status)` tuple — uses `IS NOT DISTINCT FROM` so NULL buckets are distinct, not wildcards (`backend/alembic/versions/0013_stock_nonneg_trigger.py:44-69`). Defence-in-depth for the per-part advisory lock — see [ledger](ledger.md) and [ADR-0001](../adr/0001-append-only-stock-ledger.md).
- **`parts_default_storage_workspace_check` (alembic 0036).** BEFORE trigger on `parts`. Rejects an insert/update where `default_storage_location_id` points at a `storage_locations` row in a different workspace. Raises with `ERRCODE = '23514'` so SQLAlchemy surfaces it as `IntegrityError` (`backend/alembic/versions/0036_parts_default_storage_ws_trigger.py:27-50`). This is the **single** DB-enforced workspace-isolation rule — every other check is in code. See [workspace-isolation](workspace-isolation.md) and [ADR-0002](../adr/0002-code-enforced-workspace-isolation.md).
- **CHECK constraints on `order_entries` and `project_entries`** (alembic 0032): `quantity_ordered >= 0`, `quantity_received >= 0`, `quantity >= 0` (`backend/app/domain/orders/models.py:51-52`, `backend/app/domain/projects/models.py:65`).

## Partial-unique indexes

These mediate the soft-archive contract: archiving a row frees its name/MPN for re-use.

| Index | Predicate | Source |
|---|---|---|
| `uq_parts_ws_mpn` | `mpn IS NOT NULL AND archived_at IS NULL` | `backend/app/domain/parts/models.py:33-39` |
| `uq_storage_ws_name` | `archived_at IS NULL` | `backend/app/domain/storage/models.py:15-21` |
| `uq_tag_ws_name` | `archived_at IS NULL` | `backend/app/domain/tags/models.py:15-21` |
| `uq_workspace_invitation_pending` | `status = 'pending'` | `backend/app/domain/workspaces/models.py:77-83` |
| `uq_catalog_tokens_ws_hmac_active` | `revoked_at IS NULL` | `backend/app/domain/workspaces/models.py:124-130` |
| `ix_workspaces_catalog_token_hash` | `catalog_token_hash IS NOT NULL` | `backend/app/domain/workspaces/models.py:18-23` |
| `ix_stock_ws_bag_signature` | `bag_signature IS NOT NULL` | `backend/app/domain/stock/models.py:40-45` |

See [ADR-0004](../adr/0004-mpn-uniqueness-per-workspace.md) for the MPN-uniqueness rule.

## Migration history

Schema evolves forward-only. The chain runs `0001_initial.py` → `0036_parts_default_storage_ws_trigger.py`. Two migrations in this chain are load-bearing for the invariants on this page:

- `0013_stock_nonneg_trigger.py` — ledger non-negative trigger.
- `0036_parts_default_storage_ws_trigger.py` — workspace-isolation trigger on `parts.default_storage_location_id`.

Other notable ones cross-referenced from this doc set: `0011` (MPN unique index), `0012` + `0020` (`bag_signature` column + partial index), `0018` (cross-domain SET NULL FKs + partial unique on storage/tag names + pg_trgm GIN), `0030` (`audit_log`), `0031` (poly-orphan-cleanup indexes), `0032` (integer-quantity CHECKs), `0034` (`bulk_import_idempotency`), `0035` (`workspace_catalog_tokens`).

Don't edit a merged migration — add a new one. (`CLAUDE.md` Migrations section.)
