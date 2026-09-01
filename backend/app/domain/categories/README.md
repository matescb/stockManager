# categories

Audience: engineer

Owns `PartCategory` — the workspace-scoped bucket a part belongs to (resistors, MCUs, connectors…). Carries the KiCad metadata (reference-designator prefix, default symbol / footprint refs, chooser filters, library slug) that a later phase serves over the KiCad HTTP-library protocol.

## Files

| File | What |
|---|---|
| `models.py` | `PartCategory` |
| `schemas.py` | `PartCategoryIn` / `PartCategoryPatch` / `PartCategoryOut` |
| `service.py` | List / create / update / archive / restore + `slugify` |

## Public surface

| Operation | Entry point |
|---|---|
| List (active or including archived) | `service.py::list_categories` |
| Fetch one (archived included) | `service.py::get_category` |
| Create / update | `service.py::create_category`, `::update_category` |
| Archive / restore | `service.py::archive_category`, `::restore_category` |
| Derive a library slug from free text | `service.py::slugify` |

REST surface: `backend/app/api/routes/categories.py` (`/api/categories`).

## Hard rules (this module)

1. **Name and `library_slug` are unique per workspace among active rows.** Partial unique indexes `uq_part_categories_ws_name` and `uq_part_categories_ws_slug` (`WHERE archived_at IS NULL`, alembic 0067) — the same shape `tags` uses. Archiving frees both for re-use, so `restore_category` re-checks and returns `409` with `existing_id` when they've been taken.
2. **Uniqueness is case-sensitive**, matching `tags`. "Resistors" and "resistors" are two categories; they collide on the derived slug, not the name.
3. **A rename does not move `library_slug`.** The slug is the stable identifier a KiCad library nickname is built from; it only changes when the caller passes one explicitly.
4. **`parts.category_id` is `ON DELETE SET NULL`.** Categories are archived, not deleted, but a hard delete must never cascade into the parts table.

## See also

- [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md) — workspace isolation is the caller's job
- [api/routes README](../../api/routes/README.md) — router → docs map

## Don't

- Don't re-derive `library_slug` on rename — downstream KiCad library references are built from it.
- Don't add a case-insensitive unique index without migrating the existing rows first; `tags` sets the precedent and both surfaces should move together.
- Don't hard-delete a category to "clear" it from parts — archive it, so the audit trail and `parts.category_id` survive.
