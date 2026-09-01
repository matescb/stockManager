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
derived from `name` when omitted, stable across renames), `archived_at`.

Uniqueness: `name` and `library_slug` are each unique per workspace among
**active** rows (partial unique indexes, migration `0067`) — archiving frees
both for re-use.

## Routes

### `GET /api/categories`

| Field | Type | Required | Notes |
|---|---|---|---|
| `include_archived` | bool | no | Default `false`. |
| `limit` | int | no | Default `200`, max `1000`. |

Ordered by `sort_order`, then `name`.

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
since been claimed by another active row. Audit: `category.archived` /
`category.restored`.

## Parts wiring

`parts.category_id` (nullable, `ON DELETE SET NULL`) is set through the parts
routes. A foreign-workspace id 404s (`assert_in_workspace`); an archived
category is `409 code=category.archived` — but only when the value *changes*,
so a part pointing at a since-archived category stays patchable. A BEFORE
trigger (`parts_category_workspace_check`, migration `0067`, SQLSTATE `WS001`)
backstops raw SQL.

## Source

`backend/app/api/routes/categories.py`, `backend/app/domain/categories/`,
tests in `backend/tests/test_categories.py`.
