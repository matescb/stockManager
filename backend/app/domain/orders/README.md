# orders

Audience: engineer

Owns purchase orders and the receive workflow that turns an order line into ledger writes (and, when applicable, a new `Lot`).

## Files

| File | What |
|---|---|
| `models.py` | `Order`, `OrderEntry` |
| `schemas.py` | Pydantic shapes for order CRUD + receive payloads |
| `service.py` | `receive` orchestration + `_order_status` derivation |

## Public surface

| Operation | Entry point |
|---|---|
| Receive (fully or partially) | `service.py::receive` |
| Derive order status from entries | `service.py::_order_status` (internal helper, called by routes / receive) |

`Order.status` is derived from the per-entry received vs ordered quantities; it isn't a separately stored truth.

## Hard rules (this module)

1. **Receive writes go through `domain/stock/service.py`.** Never insert `stock_entries` rows from this module directly. See [ADR-0001](../../../../docs/adr/0001-append-only-stock-ledger.md).
2. **Lot creation on receive is part of the same transaction as the ledger write.** A partial failure must roll both back.
3. **Workspace isolation is checked on every nested object** (`Order` → `OrderEntry` → `Part` / `Lot` / `StorageLocation`). See [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md).

## See also

- [Domain doc — orders & receive](../../../../docs/domain/orders-and-receive.md) — receive orchestration, partial receive, lot creation rules
- [Domain doc — lots & serials](../../../../docs/domain/lots-and-serials.md) — when a receive creates a Lot
- [API — orders](../../../../docs/api/orders.md) — REST surface

## Don't

- Don't compute `Order.status` outside `_order_status` — keep one source of truth.
- Don't bypass `domain/stock/service.py::add_stock` to insert ledger rows on receive.
- Don't allow re-receiving a fully-received entry without a corresponding `adjust` ledger entry — the ledger must reconcile.
