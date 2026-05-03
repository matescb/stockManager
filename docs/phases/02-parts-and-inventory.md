# Phase 2 — Parts & inventory ledger

Audience: engineer

> Note: retro-documented 2026-05-03 from migration 0001; the original
> PR predates the phase-docs convention.

Establishes the part catalogue, storage locations, lots, and the
**append-only `stock_entries` ledger** that the rest of the system
reads through (never around).

## Why

- A real inventory app needs the cross-product of "what part" × "where
  is it" × "from which batch". Modelling that as a mutable `qty`
  column on a join row makes audit impossible.
- The ledger choice (one row per stock event, current quantity is the
  sum) had to land in the first migration so no later code could grow
  a shortcut around it.
- Substitutes and meta-parts had to be expressible from day one — the
  build engine that consumes them comes later but its data model is
  already locked.

## What shipped

- `parts` — name, IPN, MPN, manufacturer, footprint, `part_type`
  (`linked / local / meta / sub_assembly`), `attrition_percentage` +
  `attrition_min_quantity`, `default_storage_location_id`,
  `default_storage_mandatory`, `low_stock_report_quantity`, `published`.
  Source: `backend/alembic/versions/0001_initial.py:192-228`.
- `part_substitutes` (`part_id`, `substitute_part_id`, `direction`)
  with `uq_part_sub` — `0001_initial.py:305-316`. `direction ∈
  {one_way, bidirectional}`; consumed by the build engine in
  [Phase 5](05-builds.md).
- `part_meta_members` join — `0001_initial.py:294-304`. CRUD lands
  in [Phase 8](08-meta-parts.md).
- `part_cad_keys` — `0001_initial.py:284-293`. Lets a CAD-tool
  designator map to a curated part during BOM import.
- `storage_locations` — `single_part_only`, `existing_parts_only`,
  `is_full` flags; `uq_storage_ws_name`. Source:
  `0001_initial.py:138-159`.
- `lots` — provenance batch: `part_id`, `name`, `serial_number`,
  `parent_lot_id` (split tracking), `expiration_date`, `source_type`
  (`purchase / build / manual / split / …`), `source_order_id`,
  `source_build_id`, `purchase_quantity`, `purchase_unit_cost`,
  `purchase_currency`. Source: `0001_initial.py:250-283`.
- `stock_entries` — the ledger. `quantity_delta` (signed Integer),
  `status` (`on_hand / reserved / consumed / …`),
  `operation_type` (`add / remove / move / receive / build_consume /
  build_produce / …`), `lot_id`, `storage_location_id`,
  `related_entry_id` (links the two rows of a move), plus FK columns
  `order_id`, `order_entry_id`, `project_id`, `build_id` that wire
  later phases in. Source: `0001_initial.py:349-381`.

## Invariants introduced

- **No `inventory.qty` column anywhere.** Current quantity is always
  `SUM(quantity_delta) WHERE status='on_hand'` over the matching
  `(part_id, lot_id?, storage_location_id?)` filter. All reads go
  through `backend/app/domain/stock/service.py::current_quantity` or
  a roll-up built on it. See `CLAUDE.md` — "No `inventory.qty`
  column" and `docs/ARCHITECTURE.md` — "The ledger model".
- **Operations are the unit of audit.** Add = +1 row. Remove = -1
  row. Move = paired -row + +row joined by `related_entry_id`.
  Adjust = `actual − current`. Never patch a previous row.
- **`stock_entries` is append-only.** No service path mutates a
  prior row; reversals are new rows with the opposite delta.
- **`Lot.parent_lot_id`** is the only correct way to model splitting
  a batch — never duplicate the source row.

## Things deferred

- Quantity precision — `quantity_delta` is Integer from day one; the
  `project_entries.quantity` Numeric → Integer cleanup is later
  (migration 0032, `0032_integer_quantities.py`).
- Non-negative-quantity database trigger — `0013_stock_nonneg_trigger.py`.
- MPN uniqueness per workspace — `0011_parts_mpn_unique.py`
  (cross-link to `../adr/` MPN-uniqueness ADR).
- Bag-signature column for re-scan recognition —
  `0012_stock_entries_bag_signature.py` (see [Phase 11](11-providers-and-scan.md)).
- Default-storage workspace trigger —
  `0036_parts_default_storage_ws_trigger.py`.

## References

- Migration: `backend/alembic/versions/0001_initial.py`
- Tables created here: `parts`, `part_substitutes`,
  `part_meta_members`, `part_cad_keys`, `storage_locations`, `lots`,
  `stock_entries`.
- Service entrypoint: `backend/app/domain/stock/service.py` —
  `current_quantity`, `add_stock`, `remove_stock`, `move_stock`.
- Architecture: `docs/ARCHITECTURE.md` — "The ledger model" and
  "Lot lifecycle".
- TODO(verify): exact set of `operation_type` literals used at
  Phase 2 vs added later — `receive` (Phase 4), `build_consume` /
  `build_produce` (Phase 5) ride on the same column.
