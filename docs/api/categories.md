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
(≤50 globs, nullable), `value_template` / `kicad_fields` (nullable —
see below), `list_columns` / `list_sort` (nullable — see
**Parts-list spec columns**), `library_slug` (lowercase `[a-z0-9-]`, ≤60, derived from
`name` when omitted, stable across renames), `parent_id` (nullable —
see below), `archived_at`.

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

## KiCad Value rules

Two columns (migration `0082`) decide what a part in this category shows on
a KiCad schematic. Neither has any effect outside
[the KiCad API](kicad.md#value-derivation).

| Column | Type | Meaning |
|---|---|---|
| `value_template` | `String(200)`, nullable | `"{resistance} {tolerance} {package}"` renders `10 kΩ 1% 0603` from the part's canonical specs. `{mpn}` resolves from the part column. |
| `kicad_fields` | JSONB list, nullable | Canonical spec keys also emitted as hidden symbol fields — `voltage_rating` becomes `Voltage Rating`. |

Validation is Pydantic, not a CHECK constraint: the vocabulary of legal keys
is application data, and a constraint would need a migration every time it
grew. A placeholder is `{[a-z_]+}` and nothing else — `{Resistance}` and
`{ resistance }` are `422`, because rendering leaves unrecognised braces as
literal text and drawing `{Resistance}` on every symbol in a category is not
a failure a user can diagnose. At most 20 placeholders and 20 `kicad_fields`
entries, each matching `^[a-z_]+$` (≤64); a repeated key is stored once,
since one key emits one field either way.

**Null means inherit, `[]` does not.** Both columns resolve up `parent_id` to
the nearest ancestor that sets them, independently of each other, so a
template on *Capacitors* covers *Capacitors / Ceramic* without being
repeated. An explicit `[]` on `kicad_fields` is a child saying "emit none"
against a parent that emits something, and stops the walk. So does an
archived ancestor. A `PATCH` with an explicit `null` clears the override —
it is not in `_NON_NULLABLE_PATCH_FIELDS`.

Nothing seeds these. A category with both unset behaves exactly as it did
before `0082`, and a template naming a key the part does not carry renders
nothing and falls back to `parts.name` — so a workspace whose specs still
hold the provider's verbatim attribute names (`Resistance`, not
`resistance`) sees no change until those rows are re-keyed.

## Parts-list spec columns

Two more columns (migration `0083`) decide what the **parts list** shows when
it is filtered to this category. Neither has any effect on the KiCad surface.

| Column | Type | Meaning |
|---|---|---|
| `list_columns` | JSONB list, nullable | Canonical spec keys to show as table columns, in order — `["resistance", "tolerance", "power"]`. At most **12**. |
| `list_sort` | JSONB record, nullable | `{"key": "<spec key>", "dir": "asc"\|"desc"}` — the sort `GET /api/parts` applies when the request names none. |

They live on the category rather than in the browser because
`DataTable`'s hidden-column map is `localStorage` (per viewer, per device,
invisible to everyone else) and "resistors show resistance, tolerance and
power" is a fact about resistors.

Validation is the same shape as the KiCad columns above — Pydantic for the
form (`^[a-z_]+$`, ≤64, deduped, capped), the service for the vocabulary.
Every key must be in **this category's effective spec schema**; one that is
not is `422 code=category.unknown_spec_key` with the offending `key` in the
detail, because a key outside the schema can only ever render a blank
column. Over the cap is `422 code=category.too_many_spec_columns` with
`max_columns`. The check runs on `POST` as well as `PATCH`, against the
schema of the row being created, so create never accepts what the next
`PATCH` refuses.

**Null means inherit, `[]` does not** — the same rule, and the same
independent walks, as `value_template` / `kicad_fields`. A `PATCH` with an
explicit `null` clears the override.

Audit rides the existing `category.updated` / `category.created` rows, whose
comment is `fields=<names>` — field names only, never the values.

### `GET /api/categories/{id}/spec-schema`

Which canonical spec keys this category's parts carry, and which of them the
parts list is configured to show. Read-only; writes go through `PATCH` above.

**Response** — `200 OK`

```json
{
  "data": {
    "slug": "capacitor_ceramic",
    "class": "capacitor",
    "keys": [
      { "key": "package", "label": "Package", "unit": null,
        "mandatory": true, "numeric": false, "common": true,
        "slugs": ["capacitor_ceramic"] },
      { "key": "capacitance", "label": "Capacitance", "unit": "F",
        "mandatory": true, "numeric": true, "common": false,
        "slugs": ["capacitor_ceramic"] }
    ],
    "list_columns": ["capacitance", "voltage_rating"],
    "list_sort": { "key": "capacitance", "dir": "asc" },
    "inherited_from": null,
    "sort_inherited_from": "…uuid…"
  },
  "status": { … }
}
```

| Field | Notes |
|---|---|
| `slug` | The [ADR-0034](../adr/0034-spec-schema.md) schema slug, or `null` — which is **not** an error. With `class` set it means the class union below; without one, the common keys only. |
| `class` | The component class the name path maps to (`capacitor`, `resistor`, `ic`, …), or `null`. Reported whether or not a slug resolved. |
| `keys` | Common keys plus the slug's own, in schema order — or the class union when `slug` is `null` and `class` is not. |
| `slugs` | Which schema slugs define this key. One entry for a single-slug category; several under a class union (`esr` is electrolytic + tantalum); empty when neither a slug nor a class resolved. |
| `unit` | SI base-unit symbol (`"Ω"`, `"F"`, `"%"`), or `null` for a value that is not a quantity. |
| `numeric` | A **display** hint (right-align). `unit != null`, plus an allow-list of unitless counts (`pin_count`, `positions`, `rows`, `channels`, `hfe`) in `spec_columns.NUMERIC_UNITLESS_KEYS`. NOT a promise that the sort is numeric — a count has no `value_num` and sorts as text. |
| `mandatory` | This category says a part must carry the key; drives `missing_specs` too. Under a class union it is true only where **every** slug in the class agrees, so `esr` (mandatory for an electrolytic, optional for a tantalum) comes back optional. |
| `common` | True for the keys every category carries, so a picker can group them apart. |
| `list_columns` / `list_sort` | The **resolved** stored choice, walked up `parent_id`. |
| `inherited_from` / `sort_inherited_from` | Which ancestor each came from, or `null` when this category owns it (or nobody does). |

**How the slug is resolved.** `spec_schema.category_slug_for` reads a
` / `-joined **name path**, so the leaf's own path answers most cases —
*Capacitors / Ceramic* is `capacitor_ceramic`. When it does not, the walk
retries with trailing segments dropped, leaf first, up to the root, and
takes the first slug that resolves; a leaf whose own words name a different
component class than its parent therefore still lands on the parent's
schema. A bare root *Capacitors* still resolves to `slug: null`
(`CLASS_DEFAULT_SLUG["capacitor"]` is `None`): a capacitor whose category
does not say ceramic, film, tantalum or electrolytic has no canonical key
set of its own.

**The class union.** That `null` is right for *writing* a part's specs and
wrong for *reading* a category. The parts filed under a bare *Capacitors*
were written by whichever slug their own import resolved, so the branch
really does carry `capacitance`, `dielectric` and `esr` — and a columns
menu built from the common keys alone offers none of them. So when the slug
walk finds nothing but the name path names a **class**, the payload answers
with the UNION of that class's slugs (`spec_class.class_slugs`), ordered:

1. the common keys, in `COMMON_SPECS` order;
2. the keys **every** slug in the class defines (`capacitance`,
   `voltage_rating`);
3. the rest in first-seen schema order.

`slug` stays `null`, `class` names the class, and each key's `slugs` says
which subtypes define it. `mandatory` survives only where every slug agrees.
**This affects `capacitor` and `transistor` roots and nothing else.** They
are the only two classes whose `CLASS_DEFAULT_SLUG` is `None`. Every other
class — resistor, inductor, diode, LED and the seven in
`spec_schema_tables_more.py` — carries its own default slug, so the walk
resolves and the union is never consulted: a bare *Resistors* answers with
the `resistor` schema and a bare *Diodes* with `diode`, exactly as before.

`PATCH /api/categories/{id}` validates `list_columns` / `list_sort` against
exactly the same vocabulary, so a key this payload offers is a key the PATCH
accepts and the listing renders. Sorting by a union key works because the
`custom_fields` join matches on the KEY, not on the slug — a
`sort=spec:capacitance` on the root orders ceramics and electrolytics
together, by number.

This is a read-side vocabulary only: it changes nothing about which slug a
part resolves to on import, what `normalise()` writes, or which keys
`missing_specs` demands.

A category from another workspace is `404 code=category.not_found`.

Source: `backend/app/domain/parts/spec_class.py`,
`backend/app/domain/parts/services/spec_columns.py`, tests in
`backend/tests/test_spec_columns.py`.

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
[Parts API](./parts.md#get-apiparts) for `category_id` /
`include_descendants`, and for `spec_columns` / `sort=spec:<key>`, which
read the schema and the stored choice described above.

## Source

`backend/app/api/routes/categories.py`, `backend/app/domain/categories/`
(`tree.py` owns the hierarchy walks),
`backend/app/domain/parts/services/spec_columns.py` (the spec-column
resolution, validation and sort), tests in
`backend/tests/test_categories.py`,
`backend/tests/test_category_tree.py` and
`backend/tests/test_spec_columns.py`.
