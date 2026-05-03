# Phase 8 — Meta-parts & sub-assemblies

Audience: engineer

The schema already supported four `Part.part_type` values (`linked`,
`local`, `meta`, `sub_assembly`) and a `part_meta_members` join table.
Phase 8 adds CRUD for meta members and lets the build engine pull
stock from any member when consuming a meta-part BOM line.

## Endpoints

```
GET    /api/parts/{meta_id}/members
POST   /api/parts/{meta_id}/members         { member_part_id }
DELETE /api/parts/{meta_id}/members/{member_id}
```

Constraints:
- Parent must have `part_type='meta'`.
- A meta-part cannot include itself.
- Members cannot themselves be meta-parts (one level only).
- Re-adding the same member is idempotent.

## Build engine

`backend/app/domain/builds/service.py` adds a `_candidate_part_ids()`
helper:

- For a regular part, returns its registered substitutes (one-way
  main→sub or bidirectional in either direction). Same as before.
- For a meta-part, returns its `part_meta_members` rows.

This is used in two places:

1. `shortage_analysis()` populates `substitute_ids` and
   `substitute_available` from the candidate list. For a meta-part
   entry, the meta itself usually has zero on-hand and all stock
   lives in the members; the report makes that visible.
2. `consume()` validates each consumption line: a chosen `part_id`
   must equal the entry's main part **or** appear in the candidate
   list. The error message distinguishes "not a substitute" vs
   "not a meta-part member".

## UI

- Part create: `part_type` dropdown now includes `sub_assembly`.
- Part detail SubNav: `Members` tab appears when `part.part_type === 'meta'`,
  letting the user add/remove members.

## Tests

`backend/tests/test_meta_parts.py`:
- CRUD + validation: idempotency, self-add rejected, nesting rejected,
  parent-must-be-meta rejected
- Build with a `meta_part` BOM entry consumes from members
- Non-member part rejected with the right error message
