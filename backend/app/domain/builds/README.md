# builds

Audience: engineer

Owns the `Build` aggregate: shortage analysis against a project BOM, reservation lifecycle, consume-on-build, and creation of the output lot.

## Files

| File | What |
|---|---|
| `models.py` | `Build` |
| `schemas.py` | Pydantic shapes for shortage / reserve / consume payloads |
| `service.py` | `shortage_analysis`, `apply_reservations`, `release_reservations`, `consume` |

## Public surface

| Operation | Entry point |
|---|---|
| Shortage analysis vs BOM | `service.py::shortage_analysis` |
| Apply reservations | `service.py::apply_reservations` |
| Release reservations | `service.py::release_reservations` |
| Consume + create output lot | `service.py::consume` |

Internal helpers: `_required` (per-entry qty), `_candidate_part_ids` (substitutes resolution), `_consumable_entries`.

## Hard rules (this module)

1. **Consume goes through `domain/stock/service.py::remove_stock`.** No direct ledger inserts from this module. See [ADR-0001](../../../../docs/adr/0001-append-only-stock-ledger.md).
2. **Reservations are not stock.** Reserved qty reduces *available* but not *on-hand*. The ledger is unaffected by `apply_reservations`; only `consume` writes ledger rows.
3. **Output lot creation is part of the consume transaction** — partial failure rolls everything back.

## See also

- [Domain doc — builds & BOM](../../../../docs/domain/builds-and-bom.md) — reservation model, substitutes, output_lot
- [Domain doc — ledger](../../../../docs/domain/ledger.md) — how consume rows look
- [API — builds](../../../../docs/api/builds.md) — REST surface

## Don't

- Don't compute "available qty" inside this module — call `domain/stock/service.py::available_quantity`.
- Don't release reservations implicitly on shortage recompute — release is an explicit action.
- Don't create the output lot before the consume rows are in the ledger; the lot has no meaningful content without them.
