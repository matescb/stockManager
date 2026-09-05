# builds

Audience: engineer

Owns the `Build` aggregate: shortage analysis against a project BOM, reservation lifecycle, consume-on-build (single-pass **or** multi-stage), and creation of the output lot.

## Files

| File | What |
|---|---|
| `models.py` | `Build`, `BuildStage`, `BuildStageLine` |
| `schemas.py` | Pydantic shapes for shortage / reserve / consume / stage payloads |
| `service.py` | `shortage_analysis`, `apply_reservations`, `release_reservations`, `consume` + the helpers both consume paths share |
| `stages.py` | Multi-stage builds (Track B2): stage CRUD, per-stage allocation and shortage, `consume_stage` |
| `kitting.py` | Kitting (Track B3): plan / execute a consolidation of the build's components into one staging location |
| `picklist.py` | Printable pick lists (Track B4): read-only per-line demand + the ordered shelf walk |

## Public surface

| Operation | Entry point |
|---|---|
| Shortage analysis vs BOM | `service.py::shortage_analysis` |
| Apply reservations | `service.py::apply_reservations` |
| Release reservations | `service.py::release_reservations` |
| Release part of a reservation | `service.py::release_reservation_amounts` |
| Consume + create output lot (single pass) | `service.py::consume` |
| List stages with per-stage shortage | `stages.py::stages_payload` |
| Create a stage | `stages.py::create_stage` |
| Consume one stage | `stages.py::consume_stage` |
| Preview a kit | `kitting.py::plan_kit` |
| Kit to a staging location | `kitting.py::execute_kit` |
| Printable pick list (whole build or one stage) | `picklist.py::pick_list` |

Internal helpers: `_required` (per-entry qty), `_candidate_part_ids` (substitutes resolution), `_consumable_entries`, `_outstanding_reservations` (quantity-based release accounting), `stages.py::_allocate` (cumulative portion split).

Shared by both consume paths: `lock_for_consume`, `apply_consume_lines`, `produce_output`, `complete_build`.

Kitting never writes a ledger row itself — it moves stock through `stock/service.py::move_quantity`, so total on-hand is invariant and reservations are untouched. See `docs/domain/builds-and-bom.md#kitting`.

## Hard rules (this module)

1. **Consume goes through `domain/stock/service.py::remove_stock`.** No direct ledger inserts from this module. See [ADR-0001](../../../../docs/adr/0001-append-only-stock-ledger.md).
2. **Reservations are not stock.** Reserved qty reduces *available* but not *on-hand*. The ledger is unaffected by `apply_reservations`; only `consume` writes ledger rows.
3. **Output lot creation is part of the consume transaction** — partial failure rolls everything back. A multi-stage build produces its output once, on the stage that completes the build.
4. **Reservations for a multi-stage build are still taken once, up front, for the whole build.** Creating a stage writes no ledger row; each stage consume releases only its own slice. A per-stage reservation would double-count against `reserved_quantity`.
5. **Stage quantities are slices of `_required`, never re-derived from `project_entries.quantity`** — that is what keeps attrition applied exactly once and staged consumption equal to the single-pass total.
6. **The pick list is read-only and reads stock only through `domain/stock/service.py`.** Its per-location breakdown is `bulk_stock_by_location`, a roll-up living inside the stock service; `picklist.py` grows no `GROUP BY` of its own. It writes no ledger row, so it writes no audit row either.

## See also

- [Domain doc — builds & BOM](../../../../docs/domain/builds-and-bom.md) — reservation model, substitutes, output_lot
- [Domain doc — ledger](../../../../docs/domain/ledger.md) — how consume rows look
- [API — builds](../../../../docs/api/builds.md) — REST surface

## Don't

- Don't compute "available qty" inside this module — call `domain/stock/service.py::available_quantity`.
- Don't release reservations implicitly on shortage recompute — release is an explicit action.
- Don't create the output lot before the consume rows are in the ledger; the lot has no meaningful content without them.
