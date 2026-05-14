# Workspace Isolation

Audience: engineer

The hard rule and the one DB-enforced exception. The general invariant lives in [`CLAUDE.md` — "Hard invariants"](../../CLAUDE.md); the [ADR](../adr/0002-code-enforced-workspace-isolation.md) records the decision and what it forbids.

## The rule

Workspace isolation is enforced **in code, not the database**. Every query and every cross-table FK lookup filters by `workspace_id`. There is no PostgreSQL row-level security policy on any table.

Concrete shape:

```python
# Every read filters explicitly:
db.query(Part).filter(Part.workspace_id == ws.id, ...)

# Every cross-table lookup re-validates:
storage = db.get(StorageLocation, payload.storage_location_id)
if storage is None or storage.workspace_id != ws.id:
    raise StockError("storage location not found")
```

### Route-layer helpers (canonical)

The route layer uses three helpers from `backend/app/api/_helpers.py`. **These are the workspace-isolation contract for any route that accepts a UUID**; reach for them before inlining a `db.get` + equality check:

- `assert_in_workspace(db, Model, id_, workspace_id)` (`backend/app/api/_helpers.py:22`) — look up a `WorkspaceOwned` row by id, scoped to the current workspace. Raises 404 on miss *or* on cross-workspace match. Replaces the workspace-blind `db.get(Model, id)` pattern.
- `assert_child_in_parent(db, Child, child_id, Parent, parent_id, workspace_id)` (`backend/app/api/_helpers.py:99`) — same, plus asserts the child's parent FK matches the supplied parent id.
- `assert_part_live(...)` — assert a part exists, is in the workspace, and isn't archived.

Generic-typed (`TypeVar` bound to `WorkspaceOwned`) so a typo (`Parts` instead of `Part`) is caught by the type-checker — this is load-bearing.

### Service-layer idiom

The `_belongs(obj, workspace_id)` helper (`backend/app/domain/stock/service.py:46-47`) is the canonical idiom inside the stock service. Other services inline the equality check.

The active leak this prevents — `adjust_stock` validates caller-supplied `lot_id` and `storage_location_id` against the workspace **before** the availability check, because `current_quantity` is workspace-filtered, so a foreign FK target reads as "0 available" and would otherwise let a caller persist a positive `StockEntry` in workspace A whose `lot_id` points at workspace B (`backend/app/domain/stock/service.py:721-738`).

The same defence-in-depth pattern is replicated in `remove_stock` (`backend/app/domain/stock/service.py:508-515`), `move_stock` (`backend/app/domain/stock/service.py:563-576`), `builds.consume` (`backend/app/domain/builds/service.py:367-380`), and `orders.receive` (`backend/app/domain/orders/service.py:103,118-119`).

## DB-enforced exceptions

Most workspace isolation is enforced in code. Two part-domain edges are
additionally enforced by Postgres BEFORE triggers because direct SQL could
otherwise persist a cross-workspace reference.

### `parts.default_storage_location_id`

- Migration: `backend/alembic/versions/0036_parts_default_storage_ws_trigger.py`.
- Trigger name: `parts_default_storage_workspace_check`.
- Function: `check_default_storage_workspace()`.
- Fires: `BEFORE INSERT OR UPDATE OF default_storage_location_id, workspace_id ON parts`.
- Behaviour: if `NEW.default_storage_location_id IS NOT NULL`, look up the matching `storage_locations` row by id and assert `workspace_id = NEW.workspace_id`. On mismatch, raise with `ERRCODE = '23514'` (check_violation) — SQLAlchemy surfaces this as `IntegrityError`.

Why this column gets the trigger and others don't (`backend/alembic/versions/0036_parts_default_storage_ws_trigger.py:8-16`):

> The service layer already rejects cross-workspace `default_storage_location_id` assignments. This migration adds a Postgres BEFORE trigger that provides the same guarantee at the database level, so that direct SQL (e.g. migrations, admin queries) cannot silently produce an inconsistent row.

This is the single column that's a known foot-gun for migration-time data manipulation (it was added late, has fan-out implications for stock-add validation via `default_storage_mandatory`), so it earned the belt-and-braces.

### `part_cad_keys.workspace_id`

- Migration: `backend/alembic/versions/0054_part_cad_keys_workspace_id.py`.
- Trigger name: `part_cad_keys_workspace_check`.
- Function: `check_part_cad_keys_workspace()`.
- Fires: `BEFORE INSERT OR UPDATE OF workspace_id, part_id ON part_cad_keys`.
- Behaviour: checks that `part_cad_keys.part_id` points at a `parts` row in
  `part_cad_keys.workspace_id`; mismatches raise `ERRCODE = '23514'`.

The table stores `workspace_id` directly so BOM CAD-key matching can filter
`part_cad_keys.workspace_id` before joining to `parts`. The trigger keeps that
stored workspace aligned with the owning part for migration/admin writes.

## Tables without `workspace_id`

These tables intentionally lack a `workspace_id` column. The model docstrings carry the rationale in case a well-meaning refactor tries to "fix" them:

- `users` — predates workspaces. Login is pre-workspace.
- `pending_users` — created by signup before any workspace exists (`backend/app/domain/users/models.py:91-95`).
- `user_login_failures` — protects the user credential, not a workspace resource (`backend/app/domain/users/models.py:62-64`).
- `user_sessions` — keyed by token hash; user-scoped, not workspace-scoped.

Everything else either inherits `WorkspaceOwned` (`backend/app/domain/_mixins.py:11-21`) which means `workspace_id` is `nullable=False` with `ON DELETE CASCADE`, or is an explicit child table whose workspace contract is documented here.

## Polymorphic tables

`attachments`, `custom_fields`, `tag_links` are workspace-scoped (they inherit `WorkspaceOwned`) but their `(object_type, object_id)` parent reference has **no FK** on `object_id`. Workspace isolation on these is enforced at every read AND at every cleanup query — the canonical bulk-cleanup helper `purge_polymorphic` filters by `workspace_id` on every DELETE (`backend/app/domain/_polymorphic_cleanup.py:35-79`). See [polymorphic](polymorphic.md).

## Test pin

Workspace-isolation regressions are pinned by the test pattern in `tests/test_workspace_isolation.py` style — every new endpoint that takes a workspace-scoped resource id should add a "request workspace A's resource through workspace B's session returns 404" case. (Quoted from `CLAUDE.md` — "Hard invariants".)

## What this rule forbids

- Adding `WHERE workspace_id IN (…)` joins that span workspaces "for performance".
- Trusting a caller-supplied UUID without re-checking `workspace_id` against the current session's workspace.
- Adding RLS policies and removing the application checks. (RLS would be defence-in-depth, not a replacement; the active decision is to keep the checks explicit and grep-able. See [ADR-0002](../adr/0002-code-enforced-workspace-isolation.md).)
- Using `db.get(Model, id)` and acting on the result without a `_belongs(...)` / `obj.workspace_id == ws.id` follow-up. The bare `db.get` is workspace-blind.
