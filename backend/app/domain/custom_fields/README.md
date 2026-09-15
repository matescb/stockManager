# custom_fields

Audience: engineer

Owns workspace-defined custom fields and the JSON values attached to entities. Polymorphic — a field definition can target any entity type; values are stored as a key→value JSON blob on the entity row.

## Files

| File | What |
|---|---|
| `models.py` | `CustomField` (the field definition), incl. `provider` + `value_num` (alembic 0081) |
| `schemas.py` | Pydantic shapes for field CRUD + value patch |

Per-entity value storage lives on the target entity (e.g. `Part.custom_fields` JSON column).

## Public surface

This module's surface is its model + schemas. Read/write of *values* happens on the owning entity row (in its route + service). Field definitions themselves are CRUD'd via `backend/app/api/routes/custom_fields.py`.

## Hard rules (this module)

1. **Field values live on the owning entity, not in a separate value table.** Keep this in mind when querying — there's no JOIN to a value table; you `WHERE custom_fields ->> 'key' = …`.
2. **Workspace-scoped definitions.** A `CustomField` belongs to a workspace; values referencing an unknown key are tolerated (treated as user data) but not displayed.
3. **Catalog vs spec key split.** `web/src/lib/providerCatalog.ts` and `backend/app/domain/parts/spec_schema_tables.py::CATALOG_LITERAL_KEYS` flag certain keys as catalog metadata. UI splits on this list, and `backend/tests/test_spec_schema.py` fails if the two sides drift. See [ADR-0007](../../../../docs/adr/0007-provider-catalog-vs-spec-split.md) and [ADR-0034](../../../../docs/adr/0034-spec-schema.md).
4. **`provider` and `value_num` are additive and unwritten.** Alembic 0081 adds them for the spec schema; nothing populates them until the normalisation lands in import/refresh. `value_num` is an exact `Numeric(36,18)` in the SI base unit and is serialised as a string. See [polymorphic](../../../../docs/domain/polymorphic.md).

## See also

- [Domain doc — polymorphic](../../../../docs/domain/polymorphic.md) — the no-FK surface (attachments / tags / custom_fields)
- [API — attachments / tags / custom-fields](../../../../docs/api/attachments-tags-cf.md) — REST surface
- [ADR-0007](../../../../docs/adr/0007-provider-catalog-vs-spec-split.md) — catalog vs spec key split

## Don't

- Don't add a separate "custom field values" table — the JSON column on the entity is the design.
- Don't add a catalog key on one side only — `spec_schema_tables.py::CATALOG_LITERAL_KEYS` and `web/src/lib/providerCatalog.ts` are checked against each other. The Specs / Sourcing tab split breaks otherwise.
- Don't sort or filter on `value_num` without `value_num IS NOT NULL` in the query; the supporting index is partial.
- Don't query custom-field values across workspaces; each definition is workspace-scoped.
