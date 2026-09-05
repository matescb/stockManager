# storage

Audience: engineer

Owns `StorageLocation` — the place a part / lot lives. Flat (there is no `parent_id` column; see `alembic/versions/0001_initial.py`), workspace-scoped, optionally constrained (capacity / part-type allow-list).

## Files

| File | What |
|---|---|
| `models.py` | `StorageLocation` |
| `schemas.py` | Pydantic shapes for storage CRUD |

(No `service.py` — CRUD lives in the route module; constraint enforcement lives in `domain/stock/service.py`.)

## Public surface

This module's surface is its model + schemas; reads/writes are done by callers via SQLAlchemy. Constraint checks happen in `domain/stock/service.py`.

## Hard rules (this module)

1. **`parts.default_storage_location_id` cross-workspace is blocked by a Postgres BEFORE trigger** (`parts_default_storage_workspace_check`, migration 0036). The only DB-enforced workspace check in the codebase. See [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md).
2. **Storage constraints are enforced in the stock domain**. Stock writes call `domain/stock/service.py::enforce_storage_constraints`; `StorageLocation` PATCH calls `validate_storage_constraint_flag_update` when enabling flags.
3. **Workspace isolation is code-enforced** for every other reference. See [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md).

## See also

- [Domain doc — workspace isolation](../../../../docs/domain/workspace-isolation.md) — the trigger exception
- [Domain doc — ledger](../../../../docs/domain/ledger.md) — how storage constraints gate writes
- [API — storage](../../../../docs/api/storage.md) — REST surface

## Don't

- Don't reimplement capacity / part-type checks in routes — keep them inside `domain/stock/service.py` so add / move / receive and PATCH rechecks share one path.
- Don't follow `parts.default_storage_location_id` without re-checking workspace; the trigger only catches *writes*, not joins.
- Don't assume storage locations nest — they do not. If a hierarchy is ever added here, copy the shape `part_categories` uses (`parent_id` + `ON DELETE SET NULL` + a workspace trigger, alembic 0078) and put the cycle/depth guard in the service layer, as `domain/categories/tree.py` does.
