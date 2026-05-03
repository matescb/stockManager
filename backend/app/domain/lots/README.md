# lots

Audience: engineer

Owns the `Lot` entity: a tracked batch of a single part, optionally with a parent lot (split lineage), expiry, and serial-tracked variants.

## Files

| File | What |
|---|---|
| `models.py` | `Lot` |
| `schemas.py` | Pydantic shapes for lot CRUD + split |

(No `service.py` — lot CRUD lives in the route module; lot creation on receive lives in `domain/orders/service.py::receive`; lot creation on build output lives in `domain/builds/service.py::consume`.)

## Public surface

This module's surface is its model + schemas. Lifecycle transitions are owned by callers:

| Operation | Where |
|---|---|
| Create on receive | `backend/app/domain/orders/service.py::receive` |
| Create on build output | `backend/app/domain/builds/service.py::consume` |
| Split lot | `backend/app/api/routes/lots.py` (route handler) |

## Hard rules (this module)

1. **Workspace isolation on every lookup.** `Lot.workspace_id` is required; every join from `Part` / `StorageLocation` / `StockEntry` must re-check workspace. See [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md).
2. **Splits set `parent_lot_id`** so lineage is queryable. The split itself is two ledger writes (out from parent, in to child) — see `domain/stock/service.py`.
3. **Lot quantity is derived from the ledger**, never stored on the row. See [ADR-0001](../../../../docs/adr/0001-append-only-stock-ledger.md) and `domain/stock/service.py::bulk_current_quantities_by_lot`.

## See also

- [Domain doc — lots & serials](../../../../docs/domain/lots-and-serials.md) — lifecycle, splits, serial-tracked workspaces
- [Domain doc — ledger](../../../../docs/domain/ledger.md) — how lot qty is computed
- [API — stock](../../../../docs/api/stock.md) — `/api/lots` lives here

## Don't

- Don't add a `quantity` column to `Lot` — quantity is derived (ADR-0001).
- Don't perform a split with a single ledger entry; it must be two (out + in) so history reconstructs.
- Don't follow `parent_lot_id` without filtering by `workspace_id`.
