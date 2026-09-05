# Categories API

Audience: engineer

Part categories: workspace-scoped grouping for parts, carrying per-category
EDA defaults (KiCad symbol/footprint refs, refdes prefix, footprint filters)
that later KiCad-integration phases map to libraries.

## Conventions

See [API conventions](./README.md) for envelope, errors, auth. Mounted at
`/api/categories` with `dependencies=_member_gate` (`backend/app/main.py` —
writes need member+, GETs pass for viewers). Writes are rate-limited
`30/minute` per workspace.

## Model

`PartCategoryOut` (`backend/app/domain/categories/schemas.py`): `id`, `name`
(≤120), `description` (≤500, nullable), `sort_order` (0–1 000 000),
`refdes_prefix` (≤10, nullable), `default_symbol_ref` / `default_footprint_ref`
(≤200, nullable — KiCad `LibNick:Entry` refs), `footprint_filters`
(≤50 globs, nullable), `library_slug` (lowercase `[a-z0-9-]`, ≤60,
derived from `name` when omitted, stable across renames), `parent_id`
(nullable — see below), `archived_at`.

Uniqueness: `name` and `library_slug` are each unique per workspace among
**active** rows (partial unique indexes, migration `0067`) — archiving frees
both for re-use. Both stay **workspace-global, not sibling-scoped**, now that
categories nest: `library_slug` is what `kicad_refs.py` turns into the
generated `SM_{slug}.kicad_sym` filename, so two same-named leaves under
different branches would silently collide onto one KiCad library. The cost is
that `Passives/Resistors` and `Actives/Resistors` cannot coexist.

## Hierarchy

`parent_id` (migration `0078`) is a self-referencing FK, `ON DELETE SET NULL`,
guarded by the BEFORE trigger `part_categories_parent_workspace_check`
(SQLSTATE `WS001`). It is an adjacency list; there is no closure table, no
materialized path, and deliberately **no recursive CTE** — one workspace's
`(id, parent_id)` rows are loaded once per request and walked in Python by
`backend/app/domain/categories/tree.py`.

Three rules, all enforced in the service layer:

| Rule | Response |
|---|---|
| A category cannot be its own parent | `422 code=category.parent_cycle` |
| A category cannot move under its own descendant | `422 code=category.parent_cycle` |
| Nesting is capped at **6 levels** (`tree.MAX_DEPTH`); a reparent counts the moved subtree's height, not just the moved node | `422 code=category.too_deep`, with `max_depth` |

A parent from another workspace 404s with `code=category.not_found`,
indistinguishable from a missing one. An archived parent is
`409 code=category.archived`.

**Deleting a mid-tree category promotes its children to root — it does not
cascade.** That is what `ON DELETE SET NULL` does on a hard delete, and
`archive_category` does the same thing explicitly (a soft archive does not
fire the FK action), so the two paths agree and the active tree never
contains a child whose parent is missing from it. Only **direct** children
move; grandchildren stay with their own parent.

## Routes

### `GET /api/categories`

| Field | Type | Required | Notes |
|---|---|---|---|
| `include_archived` | bool | no | Default `false`. |
| `limit` | int | no | Default `200`, max `1000`. |

Ordered by `sort_order`, then `name` — a **flat** list, not a nested one.
Clients assemble the tree from `parent_id`
(`web/src/lib/categoryTree.ts::buildCategoryTree`).

### `POST /api/categories`

`201` with the created row. `409` with `code` `category.name_conflict` or
`category.slug_conflict` plus `existing_id`/`existing_name` on an active
collision. Audit: `category.created`.

### `PATCH /api/categories/{id}`

Partial update. A rename does **not** re-derive `library_slug` (it is the
stable KiCad library identifier); pass `library_slug` explicitly to move it.
Same `409` conflict shape as create. Audit: `category.updated`.

### `POST /api/categories/{id}/archive` / `POST /api/categories/{id}/restore`

Soft archive/restore. Restore returns `409` when the freed `name`/`slug` has
since been claimed by another active row. Archive also sets every direct
child's `parent_id` to `NULL` (see **Hierarchy**); the count rides along in
the audit comment as `promoted_children=N`. A restored category always comes
back at the root. Audit: `category.archived` / `category.restored`.

## Parts wiring

`parts.category_id` (nullable, `ON DELETE SET NULL`) is set through the parts
routes. A foreign-workspace id 404s (`assert_in_workspace`); an archived
category is `409 code=category.archived` — but only when the value *changes*,
so a part pointing at a since-archived category stays patchable. A BEFORE
trigger (`parts_category_workspace_check`, migration `0067`, SQLSTATE `WS001`)
backstops raw SQL.

`GET /api/parts` filters on it — see
[Parts API](./parts.md#list-parts) for `category_id` /
`include_descendants`.

## Source

`backend/app/api/routes/categories.py`, `backend/app/domain/categories/`
(`tree.py` owns the hierarchy walks), tests in
`backend/tests/test_categories.py` and
`backend/tests/test_category_tree.py`.
