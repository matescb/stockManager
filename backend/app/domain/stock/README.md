# stock

Audience: engineer

Owns the append-only `stock_entries` ledger and every quantity read derived from it. There is no `inventory.qty` column — current stock is computed from the ledger.

## Files

| File | What |
|---|---|
| `models.py` | `StockEntry` (the only model in this module) |
| `schemas.py` | Pydantic shapes for add / remove / move / adjust + history |
| `service.py` | All ledger writes and quantity reads |

## Public surface

| Operation | Entry point |
|---|---|
| Read part qty | `service.py::current_quantity` |
| Bulk read by part | `service.py::bulk_current_quantities` |
| Bulk read by lot | `service.py::bulk_current_quantities_by_lot` |
| Reserved / available | `service.py::reserved_quantity`, `::available_quantity` |
| Write — receive / consume | `service.py::add_stock`, `::remove_stock` |
| Write — relocate | `service.py::move_stock` |
| Write — correction | `service.py::adjust_stock` |
| Take row lock before a write | `service.py::lock_parts_for_stock_write` |
| History (part / lot / storage / global) | `service.py::history_for_part`, `::history_for_lot`, `::history_for_storage`, `::history_global` |

## Hard rules (this module)

1. **Append-only.** The ledger is never updated or deleted in-place. Corrections are new `adjust` entries. See [ADR-0001](../../../../docs/adr/0001-append-only-stock-ledger.md).
2. **All quantity reads go through this module.** No route, report, or other service may compute a current quantity by summing rows itself. See [ADR-0001](../../../../docs/adr/0001-append-only-stock-ledger.md).
3. **Workspace filter on every query.** Code-enforced; there is no row-level security. See [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md).

## See also

- [Domain doc — ledger](../../../../docs/domain/ledger.md) — operation types, locking strategy, current-quantity reads
- [API — stock](../../../../docs/api/stock.md) — REST surface
- [ADR-0001](../../../../docs/adr/0001-append-only-stock-ledger.md) — why the ledger is append-only

## Don't

- Don't compute current quantity by summing `stock_entries` rows outside `service.py::current_quantity` / `bulk_current_quantities`. See ADR-0001.
- Don't UPDATE or DELETE existing ledger rows. Corrections are new `adjust` rows.
- Don't write a ledger row without first calling `service.py::lock_parts_for_stock_write` for the affected part(s) — concurrent receives can otherwise produce negative on-hand under storage constraints.
