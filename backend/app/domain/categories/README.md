# categories

Audience: engineer

Owns `PartCategory` — the workspace-scoped bucket a part belongs to (resistors, MCUs, connectors…), arranged as a tree via a self-referencing `parent_id`. Carries the KiCad metadata (reference-designator prefix, default symbol / footprint refs, chooser filters, library slug) that a later phase serves over the KiCad HTTP-library protocol.

## Files

| File | What |
|---|---|
| `models.py` | `PartCategory` |
| `schemas.py` | `PartCategoryIn` / `PartCategoryPatch` / `PartCategoryOut` |
| `service.py` | List / create / update / archive / restore + `slugify` |
| `tree.py` | Hierarchy walks — cycle guard, depth cap, descendant expansion |

## Public surface

| Operation | Entry point |
|---|---|
| List (active or including archived) | `service.py::list_categories` |
| Fetch one (archived included) | `service.py::get_category` |
| Create / update | `service.py::create_category`, `::update_category` |
| Archive / restore | `service.py::archive_category`, `::restore_category` |
| Derive a library slug from free text | `service.py::slugify` |
| Validate a create/reparent (cycle, depth, workspace, archived) | `tree.py::validate_parent` |
| Expand a category to its subtree (for `GET /parts?category_id=`) | `tree.py::descendant_ids` |

REST surface: `backend/app/api/routes/categories.py` (`/api/categories`).

## Hard rules (this module)

1. **Name and `library_slug` are unique per workspace among active rows.** Partial unique indexes `uq_part_categories_ws_name` and `uq_part_categories_ws_slug` (`WHERE archived_at IS NULL`, alembic 0067) — the same shape `tags` uses. Archiving frees both for re-use, so `restore_category` re-checks and returns `409` with `existing_id` when they've been taken.
2. **Uniqueness is case-sensitive**, matching `tags`. "Resistors" and "resistors" are two categories; they collide on the derived slug, not the name.
3. **A rename does not move `library_slug`.** The slug is the stable identifier a KiCad library nickname is built from; it only changes when the caller passes one explicitly.
4. **`parts.category_id` is `ON DELETE SET NULL`.** Categories are archived, not deleted, but a hard delete must never cascade into the parts table.
5. **`parent_id` is `ON DELETE SET NULL` too (alembic 0078), so a delete promotes children to root rather than cascading a subtree away.** `archive_category` replicates that explicitly — a soft archive does not fire the FK action, and without it the active tree would contain children whose parent is not in it. Only *direct* children move.
6. **Cycles and depth are guarded in Python, never in SQL.** `tree.py` loads one workspace's `(id, parent_id)` map and walks it. This repo has no recursive CTE anywhere and this module is not the place to introduce one — a category tree is a listing-sized set of rows, and every question falls out of a dict. Depth cap is `tree.MAX_DEPTH` (6); a reparent must count the moved subtree's height, not just the moved node.
7. **Slug and name uniqueness stay workspace-global, not sibling-scoped.** `library_slug` becomes the `SM_{slug}.kicad_sym` filename (`kicad_refs.py`), so sibling-scoping it would silently merge two branches' same-named leaves into one KiCad library. The cost — `Passives/Resistors` and `Actives/Resistors` cannot coexist — is accepted.

## See also

- [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md) — workspace isolation is the caller's job
- [api/routes README](../../api/routes/README.md) — router → docs map

## Don't

- Don't re-derive `library_slug` on rename — downstream KiCad library references are built from it.
- Don't add a case-insensitive unique index without migrating the existing rows first; `tags` sets the precedent and both surfaces should move together.
- Don't hard-delete a category to "clear" it from parts — archive it, so the audit trail and `parts.category_id` survive.
- Don't add a recursive CTE for ancestors/descendants; `tree.py` exists so the walks stay in one reviewable place. See rule 6.
- Don't let the DB trigger try to detect cycles — a BEFORE ROW trigger sees one row and cannot see the rest of a multi-statement reparent. It only checks workspace consistency.
