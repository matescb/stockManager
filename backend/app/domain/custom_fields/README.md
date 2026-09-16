# custom_fields

Audience: engineer

Owns the key/value custom fields attached to entities. Polymorphic — one row targets any entity type through an `(object_type, object_id)` pair with no FK on the id.

## Files

| File | What |
|---|---|
| `models.py` | `CustomField` (the field definition), incl. `provider` + `value_num` (alembic 0081) |
| `schemas.py` | Pydantic shapes for field CRUD + value patch |

There is no separate definition table and no JSON column on the entity: one `custom_fields` row IS one key/value pair on one object.

## Public surface

This module's surface is its model + schemas. Rows are CRUD'd via `backend/app/api/routes/custom_fields.py`.

## Hard rules (this module)

1. **Keys are free-form, not declared anywhere.** There is no registry of allowed keys — a row is created by writing it, and the catalog/spec classification below is what decides where it renders.
2. **Workspace-scoped.** A `CustomField` belongs to a workspace, and every query filters by `workspace_id`; the `(object_type, object_id)` pair has no FK, so nothing else enforces it.
3. **Catalog vs spec key split.** `web/src/lib/providerCatalog.ts` and `backend/app/domain/parts/spec_schema_tables.py::CATALOG_LITERAL_KEYS` flag certain keys as catalog metadata. UI splits on this list, and `backend/tests/test_spec_schema.py` fails if the two sides drift. See [ADR-0007](../../../../docs/adr/0007-provider-catalog-vs-spec-split.md) and [ADR-0034](../../../../docs/adr/0034-spec-schema.md).
4. **`provider` and `value_num` are additive and unwritten.** Alembic 0081 adds them for the spec schema; nothing populates them until the normalisation lands in import/refresh. `value_num` is an exact `Numeric(36,18)` in the SI base unit and is serialised as a string. See [polymorphic](../../../../docs/domain/polymorphic.md).

## See also

- [Domain doc — polymorphic](../../../../docs/domain/polymorphic.md) — the no-FK surface (attachments / tags / custom_fields)
- [API — attachments / tags / custom-fields](../../../../docs/api/attachments-tags-cf.md) — REST surface
- [ADR-0007](../../../../docs/adr/0007-provider-catalog-vs-spec-split.md) — catalog vs spec key split

## Don't

- Don't add a separate "custom field definitions" table — free-form keys on one polymorphic row are the design.
- Don't add a catalog key on one side only — `spec_schema_tables.py::CATALOG_LITERAL_KEYS` and `web/src/lib/providerCatalog.ts` are checked against each other. The Specs / Sourcing tab split breaks otherwise.
- Don't sort or filter on `value_num` without `value_num IS NOT NULL` in the query; the supporting index is partial.
- Don't query custom-field values across workspaces; each definition is workspace-scoped.
