# custom_fields

Audience: engineer

Owns workspace-defined custom fields and the JSON values attached to entities. Polymorphic — a field definition can target any entity type; values are stored as a key→value JSON blob on the entity row.

## Files

| File | What |
|---|---|
| `models.py` | `CustomField` (the field definition) |
| `schemas.py` | Pydantic shapes for field CRUD + value patch |

Per-entity value storage lives on the target entity (e.g. `Part.custom_fields` JSON column).

## Public surface

This module's surface is its model + schemas. Read/write of *values* happens on the owning entity row (in its route + service). Field definitions themselves are CRUD'd via `backend/app/api/routes/custom_fields.py`.

## Hard rules (this module)

1. **Field values live on the owning entity, not in a separate value table.** Keep this in mind when querying — there's no JOIN to a value table; you `WHERE custom_fields ->> 'key' = …`.
2. **Workspace-scoped definitions.** A `CustomField` belongs to a workspace; values referencing an unknown key are tolerated (treated as user data) but not displayed.
3. **Catalog vs spec key split.** `web/src/lib/providerCatalog.ts` (and the server-side equivalent in `backend/app/domain/parts/`) flag certain keys as catalog metadata. UI splits on this list. See [ADR-0007](../../../../docs/adr/0007-provider-catalog-vs-spec-split.md).

## See also

- [Domain doc — polymorphic](../../../../docs/domain/polymorphic.md) — the no-FK surface (attachments / tags / custom_fields)
- [API — attachments / tags / custom-fields](../../../../docs/api/attachments-tags-cf.md) — REST surface
- [ADR-0007](../../../../docs/adr/0007-provider-catalog-vs-spec-split.md) — catalog vs spec key split

## Don't

- Don't add a separate "custom field values" table — the JSON column on the entity is the design.
- Don't add a server-side catalog key without updating `web/src/lib/providerCatalog.ts` (and vice versa). The Specs / Sourcing tab split breaks otherwise.
- Don't query custom-field values across workspaces; each definition is workspace-scoped.
