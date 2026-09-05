# codes

Audience: engineer

Owns `ObjectCode` — the universal short code that makes an object scannable.
One central polymorphic table (`object_codes`) rather than a `code` column on
five tables: uniqueness has a single scope, the resolver is one query, and a
row that never gets labelled never pays for a column.

## Files

| File | What |
|---|---|
| `models.py` | `ObjectCode`, `CodeEntityType` / `CODE_ENTITY_TYPES`, the CHECK text |
| `schemas.py` | `ObjectCodeIn` / `ObjectCodeOut` |
| `service.py` | `generate_code`, `normalize_code`, `mint_or_get`, `resolve` |

## Public surface

| Operation | Entry point |
|---|---|
| Get-or-create an object's code | `service.py::mint_or_get` |
| Resolve a scanned code | `service.py::resolve` |
| Draw a fresh code | `service.py::generate_code` |
| Canonicalise typed/scanned input | `service.py::normalize_code` |

REST surface: `backend/app/api/routes/codes.py` (`/api/codes`), documented in
[docs/api/codes.md](../../../../docs/api/codes.md).

## Hard rules (this module)

1. **`entity_id` has no FK.** Hard-delete cleanup runs through
   `domain/_polymorphic_cleanup.py`, which registers `object_codes`
   alongside `attachments` / `custom_fields` / `tag_links`. Bypassing those
   listeners leaves a code resolving to a row that no longer exists.
2. **Codes are unique per workspace, not globally** (`uq_object_codes_ws_code`).
   That is what keeps them eight characters long. Every read filters on
   `workspace_id`.
3. **One code per object, forever** (`uq_object_codes_ws_entity`). Minting is
   get-or-create; the constraint is also what makes it safe under concurrency.
4. **Validate the entity before minting.** `mint_or_get` calls
   `assert_polymorphic_in_workspace` first — otherwise a caller could mint a
   code against another tenant's UUID and resolve it.
5. **`entity_type` is a closed set** (`CODE_ENTITY_TYPES`), pinned by a CHECK
   constraint. `project` is intentionally not codeable.

## See also

- [docs/api/codes.md](../../../../docs/api/codes.md) — REST reference and code format rationale
- [docs/domain/polymorphic.md](../../../../docs/domain/polymorphic.md) — the polymorphic-table contract
- [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md) — workspace isolation

## Don't

- Don't encode the UUID (or a counter) into the code — it leaks object ids and counts.
- Don't answer "unknown code" and "code owned by another workspace" differently.
- Don't add a `code` column to `parts` / `lots` / `orders` / `builds` /
  `storage_locations` "for convenience" — two sources of truth for the same
  handle is the trap this table exists to avoid.
