# Polymorphic Tables

Audience: engineer

`attachments`, `custom_fields`, and `tag_links` are the three cross-cutting tables that reference their parent via a `(object_type, object_id)` pair. There is intentionally **no FK on `object_id`** — a single FK would bind the column to one parent table, defeating the polymorphic design. This page covers the contract, the orphan-cleanup helper, and the indexing strategy.

For the architectural rationale see [`ARCHITECTURE.md` — "Polymorphic tables contract"](../ARCHITECTURE.md#polymorphic-tables-contract).

## The three tables

| Model | Table | Source |
|---|---|---|
| `Attachment` | `attachments` | `backend/app/domain/attachments/models.py:10` |
| `CustomField` | `custom_fields` | `backend/app/domain/custom_fields/models.py:10` |
| `TagLink` | `tag_links` | `backend/app/domain/tags/models.py:28` |

Common shape:

| Column | Notes |
|---|---|
| `workspace_id` | FK, **CASCADE**. Inherited from `WorkspaceOwned`. |
| `object_type` | `varchar(40)`. The discriminator. Registered values are `part`, `order`, `project`, `build`, `lot`, `storage_location`. No DB enum — the value is decided at the call site. |
| `object_id` | `UUID`. **No FK.** Free-form. |
| `archived_at` | Universal soft-archive. |

`Tag` (`backend/app/domain/tags/models.py:10`) is the workspace-scoped tag definition; `TagLink` is the join from a tag to a polymorphic parent.

`Attachment` adds `file_name`, `file_type` (default `other`), `mime_type`, `size_bytes`, `storage_key`, `uploaded_by`. `CustomField` adds `key`, `value`, `source` (`manual | provider | override`), `original_value`.

## Why no FK on `object_id`

If `object_id` had a FK, that FK would be to one parent table. A polymorphic table reaches many parent types. Multiple-FK alternatives (one nullable FK per parent type, e.g. `part_id`, `order_id`, `project_id`, …) were rejected because:

- The set of attachable types grows over time; every new type would need a schema migration on three tables.
- The "exactly one of N FKs is non-NULL" invariant has to be enforced by application code anyway — a CHECK constraint is verbose and brittle.
- Cross-cutting reads (e.g. "all attachments for this object") get a uniform shape.

Trade-off: **no DB-level ON DELETE behaviour.** The application owns cleanup through SQLAlchemy `before_delete` listeners — see below.

## Orphan-cleanup helper

`backend/app/domain/_polymorphic_cleanup.py::purge_polymorphic`.

Signature:

```python
counts = purge_polymorphic(
    db,
    workspace_id=ws.id,
    object_type="part",
    object_id=part.id,
)
# counts == {"attachments": N, "custom_fields": M, "tag_links": K}
```

Behaviour:

- Bulk DELETE on each of the three tables, filtered by `(workspace_id, object_type, object_id)`. **Always filters by `workspace_id`** — required by the workspace-isolation invariant.
- Returns a dict of deleted-row counts so callers can log observability data.
- Bulk DELETE rather than per-row ORM fetch — stays fast for objects with hundreds of attachments/fields/links.
- Idempotent: a second call with the same parameters returns `{0, 0, 0}`.

Source: `backend/app/domain/_polymorphic_cleanup.py`. The helper is used by the registered `before_delete` listeners for `Part`, `Order`, `Project`, `Build`, `Lot`, and `StorageLocation`; `backend/app/domain/all_models.py` and `backend/app/api/_helpers.py` register the listeners at import time. Soft-archive paths do not invoke it.

## Indexing

Each polymorphic table carries the same family of indexes (added incrementally via alembic 0018, 0031). All include the `workspace_id` prefix.

| Index | Predicate / scope | Purpose |
|---|---|---|
| `ix_*_object` | `(workspace_id, object_type, object_id)` | Lookup all polymorphic rows for one specific object. |
| `ix_*_ws_archived` | `(workspace_id, archived_at) WHERE archived_at IS NULL` | Universal active-row filter. (DB-004) |
| `ix_*_ws_objid_only` | `(workspace_id, object_id)` | Orphan-cleanup queries when sweeping a deleted parent's id without the `object_type` filter. (DB-006 / alembic 0031) |

Sources: `backend/app/domain/attachments/models.py:13-26`, `backend/app/domain/custom_fields/models.py:13-27`, `backend/app/domain/tags/models.py:30-45`.

`custom_fields` has an additional uniqueness constraint: `uq_cf_unique` on `(workspace_id, object_type, object_id, key)` — one custom field per object per key (`backend/app/domain/custom_fields/models.py:13`).

`tag_links` has `uq_tag_link` on `(workspace_id, tag_id, object_type, object_id)` — a tag can be applied to a given object at most once (`backend/app/domain/tags/models.py:31`).

## `CustomField.source` lifecycle

`source: String(20)`, default `manual` (`backend/app/domain/custom_fields/models.py:33-37`):

| Value | Meaning |
|---|---|
| `manual` | User-entered. Default for legacy and new manual rows. |
| `provider` | Supplied by an external data source (e.g. Mouser/DigiKey lookup). |
| `override` | User edited a row that was originally `provider`. The upstream value is preserved in `original_value` so the next refresh can detect divergence and choose not to overwrite. |

The catalog vs spec split (see [providers](providers.md)) operates on `provider`-source rows by key name — the source field tells you *who* wrote the row; the catalog/spec classification tells you *which UI tab* renders it.

## `Tag` vs `TagLink`

`Tag` is the definition (`name`, `color`, workspace-scoped, partial-unique on active name). `TagLink` is the join row to a parent.

Cascade: `tag_links.tag_id → tags.id ON DELETE CASCADE` (`backend/app/domain/tags/models.py:47`). Hard-deleting a tag removes its links automatically. Soft-archiving a tag does not.

## Service entry points

There is no dedicated `polymorphic/service.py`. The helpers live at module level:

| Operation | Entry point | Notes |
|---|---|---|
| Bulk orphan cleanup | `domain/_polymorphic_cleanup.py::purge_polymorphic` | Workspace-filtered bulk DELETE on all three tables. Returns per-table counts. |
| Hard-delete listener registration | `domain/_polymorphic_cleanup.py::register_polymorphic_cleanup_listeners` | Idempotently attaches `before_delete` listeners for registered parent models. |
| One-off orphan backfill | `scripts/purge_polymorphic_orphans.py` | Dry-run by default; pass `--apply` to delete orphan rows for registered object types. |
| Attachment CRUD | `api/routes/attachments.py` | TODO(verify): list the create/list/delete operations. |
| Custom-field CRUD | `api/routes/custom_fields.py` | TODO(verify): same. |
| Tag CRUD + link/unlink | `api/routes/tags.py` | TODO(verify): same. |

## Things to never do

- **Never add a FK on `object_id`.** That collapses the polymorphism. If you need referential integrity for one specific object type, add a separate single-purpose table.
- **Never write a polymorphic DELETE without the `workspace_id` filter.** `purge_polymorphic` is the model. Bypassing it (e.g. ad-hoc `DELETE FROM attachments WHERE object_id = …`) breaks the workspace-isolation invariant.
- **Never trust `object_type` from a request.** Treat the discriminator as a string the route handler decides; never echo a client-supplied value.
- **Never overwrite a `source='override'` row's `original_value`.** It's the only record that the row was originally provider-supplied.
- **Never assume `tag_links.tag_id` cleanup happens on tag soft-archive.** Only hard-delete cascades; archive leaves the links and the link queries should filter by `Tag.archived_at IS NULL` instead.
