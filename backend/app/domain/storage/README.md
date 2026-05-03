# storage

Audience: engineer

Owns `StorageLocation` — the place a part / lot lives. Hierarchical (`parent_id`), workspace-scoped, optionally constrained (capacity / part-type allow-list).

## Files

| File | What |
|---|---|
| `models.py` | `StorageLocation` |
| `schemas.py` | Pydantic shapes for storage CRUD |

(No `service.py` — CRUD lives in the route module; constraint enforcement on stock writes lives in `domain/stock/service.py::_enforce_storage_constraints`.)

## Public surface

This module's surface is its model + schemas; reads/writes are done by callers via SQLAlchemy. Constraint checks happen in `domain/stock/service.py`.

## Hard rules (this module)

1. **`parts.default_storage_location_id` cross-workspace is blocked by a Postgres BEFORE trigger** (`parts_default_storage_workspace_check`, migration 0036). The only DB-enforced workspace check in the codebase. See [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md).
2. **Storage constraints (capacity / allowed part types) are enforced on stock writes**, not on `StorageLocation` updates. The check lives in `domain/stock/service.py::_enforce_storage_constraints`.
3. **Workspace isolation is code-enforced** for every other reference. See [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md).

## See also

- [Domain doc — workspace isolation](../../../../docs/domain/workspace-isolation.md) — the trigger exception
- [Domain doc — ledger](../../../../docs/domain/ledger.md) — how storage constraints gate writes
- [API — storage](../../../../docs/api/storage.md) — REST surface

## Don't

- Don't enforce capacity / part-type constraints in routes — keep it inside `domain/stock/service.py::_enforce_storage_constraints` so add / move / receive all share one path.
- Don't follow `parts.default_storage_location_id` without re-checking workspace; the trigger only catches *writes*, not joins.
- Don't allow a storage location to become its own ancestor via `parent_id` — guard at the schema level.
