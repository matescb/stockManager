# Reports API

Audience: engineer

Read-only aggregate queries: low-stock parts, BOM shortage at a planned build
quantity, BOM buyability by project, sourcing risk, stock value, replenishment
cost, and expiring lots.

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. Mounted at
`/api/reports` (`backend/app/main.py:376`). All quantity reads funnel through
`domain/stock/service.py::bulk_current_quantities` /
`bulk_current_quantities_by_lot` so the SUM-of-delta invariant lives in one
place (BE2-005, [ADR-0001](../adr/0001-append-only-stock-ledger.md)).

## Routes

### `GET /api/reports/low-stock`

Live parts whose `available = on_hand - reserved` is below their
`low_stock_report_quantity`. Parts without a threshold are skipped.

**Query**

| Field | Type | Required | Notes |
|---|---|---|---|
| `include_sourcing` | boolean | no | Default `false`. When `true`, enriches rows from the workspace TrustedParts cache/search path with a 4-hour TTL and dashboard cache preference. |

**Response** - `200 OK` - array sorted by `short_by DESC`:

```json
{ "data": [ {
    "part_id": "...", "name": "...", "manufacturer": "...", "mpn": "...",
    "on_hand": 12, "reserved": 5, "available": 7,
    "threshold": 20, "short_by": 13
} ], "status": { ... } }
```

With `include_sourcing=true`, `data` is an object with `rows`,
`sourcing_status`, `powered_by`, and TrustedParts `links`. Sourcing failures do
not change the HTTP status; the report still returns `200 OK` with low-stock
rows and `sourcing: null` where enrichment was unavailable.

### `GET /api/reports/bom-shortage`

Project-wide shortage analysis at a given build quantity. No build is created;
same engine as the Build detail page.

**Query**

| Field | Type | Required | Notes |
|---|---|---|---|
| `project_id` | UUID | yes | |
| `quantity` | int (>0) | no | Default `1`. |

**Response** - `200 OK`

```json
{ "data": {
    "project_id": "...", "quantity": 10,
    "rows": [ ... ],
    "total_short": 42
}, "status": { ... } }
```

`rows` is whatever `shortage_analysis` returns. TODO(verify): exact per-row
shape (likely `{ project_entry_id, part_id, required, available, short_by,
candidates? }`).

**Errors** - `404 report.project_not_found`.

### `GET /api/reports/bom-buyability`

Workspace-wide scoreboard of active projects at one build quantity. The report
calls Source-BOM with TrustedParts cached mode enabled and degrades to
stock-only rows when sourcing is not configured, budget-blocked, or temporarily
unavailable.

**Query**

| Field | Type | Required | Notes |
|---|---|---|---|
| `build_quantity` | int (`>= 1`) | no | Default `1`. `0` or negative values return `422`. |

**Response** - `200 OK`

```json
{ "data": {
    "build_quantity": 2,
    "sourcing_status": "ok",
    "truncated": false,
    "project_cap": 50,
    "rows": [ {
      "project_id": "...", "project_name": "Amplifier",
      "can_build_now": 1, "can_build_after_purchase": 2,
      "blocking_lines_count": 0, "est_purchase_cost": "12.50",
      "partial": false
    } ]
}, "status": { ... } }
```

`sourcing_status` is `ok`, `not_configured`, `partial`, or `budget_blocked`.
Workspaces with more than 50 active projects return the newest 50 and
`truncated: true`.

### `GET /api/reports/sourcing-risk`

Workspace-wide sourcing risk for active parts with an MPN. The route uses
TrustedParts through the sourcing service with a 4-hour cache TTL, returns a
top-level `sourcing_status`, and recomputes flags on each request.

**Query**

| Field | Type | Notes |
|---|---|---|
| `only_with_flags` | bool | Default `true`; when true, clean rows are omitted. |
| `use_cached_data` | bool \| null | Default `null`; null uses cached TrustedParts data. |

**Response** - `200 OK` (envelope: `{ data, status }`)

```json
{ "data": {
  "sourcing_status": { "state": "ok", "message": "OK" },
  "rows": [ { "mpn": "STM32F103", "risk_flags": ["single_source"] } ]
}, "status": { ... } }
```

`sourcing_status.state` is `ok`, `not_configured`, `budget_blocked`, or
`upstream_error`. Non-`ok` states are reported in the payload so the report page
can render a banner without violating the API envelope.

**Risk flags**

| Flag | Heuristic |
|---|---|
| `single_source` | Exactly one distributor has authorized stock for the part MPN. |
| `no_authorized_stock` | No distributor has authorized stock and the part has internal on-hand quantity. |
| `moq_overbuy` | Best offer MOQ is greater than `5 * typical_reorder_quantity`, where typical is `max(part.low_stock_report_quantity, 10)`. |
| `lead_time_long` | Best offer lead time is greater than 30 days. |
| `preferred_distributor_unmet` | Workspace preferred distributors are configured, but none has stock. |
| `price_delta` | Best replacement price is at least 25% above the latest historical lot purchase price in the same currency. |

### `GET /api/reports/stock-value`

Sum of `lot.purchase_unit_cost * current_qty_in_lot` across all on-hand stock,
broken down by currency and by part. Lots without a recorded purchase cost
contribute `0`.

### `GET /api/reports/replenishment-cost`

Transient replacement-cost view for each on-hand part with an MPN. Historical
cost comes from current on-hand lot quantities and `lot.purchase_unit_cost`;
replacement cost comes from the best current TrustedParts offer. TrustedParts
prices are recomputed through the sourcing cache on each request and are not
stored in a report table.

**Query**

| Field | Type | Notes |
|---|---|---|
| `sort` | `delta_pct \| delta_abs \| name` | Default `delta_pct`. |
| `use_cached_data` | bool | Optional TrustedParts dashboard-cache preference override. |

**Response** - `200 OK`

```json
{ "data": {
    "rows": [ {
      "part_id": "...", "name": "...", "mpn": "STM32F103C8T6",
      "on_hand": 10,
      "historical_costs": [ { "currency": "EUR", "value": "5.000000" } ],
      "replacement_cost": "7.50",
      "delta_abs": "2.500000",
      "delta_pct": "50.00",
      "reason": null,
      "source": "trustedparts"
    } ],
    "totals": [ { "currency": "EUR", "historical_cost": "5.000000", "replacement_cost": "7.50", "delta_abs": "2.500000" } ],
    "sourcing_status": { "state": "ok", "powered_by": "TrustedParts" }
}, "status": { ... } }
```

`reason` is `currency_mismatch` when the TrustedParts offer currency does not
match any historical lot currency for the part. Sourcing configuration, budget,
auth, rate-limit, timeout, and upstream failures return `200 OK` with
`sourcing_status.state` set and row replacement fields left null.

### `GET /api/reports/expiring-lots`

Lots that have on-hand quantity > 0 and an `expiration_date` within the next
`days` days (or already past).

**Query**

| Field | Type | Notes |
|---|---|---|
| `days` | int | Default `90`, `0 <= days <= 3650`. |

**Response** - `200 OK` - array sorted by `expiration_date ASC`.

## TODOs

- TODO(verify): `shortage_analysis` per-row shape (`domain/builds/service.py:77`).
