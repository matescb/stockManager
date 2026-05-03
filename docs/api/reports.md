# Reports API

Audience: engineer

Read-only aggregate queries: low-stock parts, BOM shortage at a planned build quantity, stock value, and expiring lots.

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. Mounted at `/api/reports` (`backend/app/main.py:376`). All quantity reads funnel through `domain/stock/service.py::bulk_current_quantities` / `bulk_current_quantities_by_lot` so the SUM-of-delta invariant lives in one place (BE2-005, [ADR-0001](../adr/0001-append-only-stock-ledger.md)).

## Routes

### `GET /api/reports/low-stock`

Live parts whose `available = on_hand - reserved` is below their `low_stock_report_quantity`. Parts without a threshold are skipped.

**Response** — `200 OK` — array sorted by `short_by DESC`:

```json
{ "data": [ {
    "part_id": "...", "name": "...", "manufacturer": "...", "mpn": "...",
    "on_hand": 12, "reserved": 5, "available": 7,
    "threshold": 20, "short_by": 13
} ], "status": { ... } }
```

**Notes**

- Filters `archived_at IS NULL` (`reports.py:34`).
- Source: `backend/app/api/routes/reports.py:26-69`.

### `GET /api/reports/bom-shortage`

Project-wide shortage analysis at a given build quantity. No build is created — same engine as the Build detail page.

**Query**

| Field | Type | Required | Notes |
|---|---|---|---|
| `project_id` | UUID | yes | |
| `quantity` | int (>0) | no | Default `1`. |

**Response** — `200 OK`

```json
{ "data": {
    "project_id": "...", "quantity": 10,
    "rows": [ ... ],
    "total_short": 42
}, "status": { ... } }
```

`rows` is whatever `shortage_analysis` returns. TODO(verify): exact per-row shape (likely `{ project_entry_id, part_id, required, available, short_by, candidates? }`).

**Errors** — `404 report.project_not_found` (`reports.py:81-83`).

**Notes**

- Source: `backend/app/api/routes/reports.py:72-86`.
- Service: `backend/app/domain/builds/service.py::shortage_analysis`.

### `GET /api/reports/stock-value`

Sum of `lot.purchase_unit_cost * current_qty_in_lot` across all on-hand stock, broken down by currency and by part. Lots without a recorded purchase cost contribute `0`.

**Response** — `200 OK`

```json
{ "data": {
    "by_currency": [ { "currency": "USD", "value": 12345.6789 } ],
    "by_part":     [ { "part_id": "...", "name": "...", "on_hand": 100, "value": 42.0, "currency": "USD" } ]
}, "status": { ... } }
```

A part with lots in multiple currencies has `currency: "MIXED"` (`reports.py:126-127`).

**Notes**

- Sorted: `by_currency` by currency code, `by_part` by `value DESC`.
- Source: `backend/app/api/routes/reports.py:89-136`.

### `GET /api/reports/expiring-lots`

Lots that have on-hand quantity > 0 and an `expiration_date` within the next `days` days (or already past).

**Query**

| Field | Type | Notes |
|---|---|---|
| `days` | int | Default `90`, `0 <= days <= 3650`. |

**Response** — `200 OK` — array sorted by `expiration_date ASC`:

```json
{ "data": [ {
    "lot_id": "...", "name": "...",
    "part_id": "...", "part_name": "...",
    "on_hand": 25,
    "expiration_date": "2026-07-01",
    "days_until_expiry": 60,
    "expired": false
} ], "status": { ... } }
```

**Notes**

- `expired: true` when `days_until_expiry < 0`.
- Source: `backend/app/api/routes/reports.py:139-178`.

## TODOs

- TODO(verify): `shortage_analysis` per-row shape (`domain/builds/service.py:77`).
