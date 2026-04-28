# Phase 6 — Reports

Read-only roll-ups across the existing data. No schema changes.

## Endpoints

```
GET /api/reports/low-stock
GET /api/reports/stock-value
GET /api/reports/bom-shortage?project_id=…&quantity=…
GET /api/reports/expiring-lots?days=90
```

### Low stock
Parts whose on-hand is below their `low_stock_report_quantity`. Parts
without a threshold are skipped. Sorted by `short_by` desc.

### Stock value
Sum of `lot.purchase_unit_cost × on_hand_in_lot` across all on-hand
stock, broken down by currency. Parts whose lots have multiple
currencies show `MIXED` in the per-part view (rare, but possible if a
part is sourced under different currencies). Lots without a purchase
cost contribute zero (and the per-part `currency` may be null).

### BOM shortage
Same engine as the build-detail shortage analysis (substitute pool,
attrition floor) but ad-hoc — no build is created. Useful for asking
"can we afford a build of N units of project P right now?"

### Expiring lots
Lots with `expiration_date <= today + days` that still have on-hand
stock. Includes a `days_until_expiry` (negative for expired) and an
`expired` flag.

## UI

`/reports` with sub-tabs at `/reports`, `/reports/value`,
`/reports/bom`, `/reports/expiring`. All tables are exportable via
`DataTable.exportFilename`.

## Tests

`backend/tests/test_reports.py` — 4 tests:
- low-stock excludes parts without threshold and parts above threshold
- stock-value sums per currency correctly
- bom-shortage matches the build engine
- expiring-lots filters by configurable window
