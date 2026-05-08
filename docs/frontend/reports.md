# Reports Frontend

Audience: engineer

Frontend report routes and their data-fetching conventions.

## Routes

| Route | Component | API |
|---|---|---|
| `/reports` | `web/src/routes/reports/Reports.tsx` | `GET /api/reports/low-stock` |
| `/reports/buyability` | `web/src/routes/reports/BomBuyabilityReport.tsx` | `GET /api/reports/bom-buyability` |
| `/reports/sourcing-risk` | `web/src/routes/reports/SourcingRiskReport.tsx` | `GET /api/reports/sourcing-risk` |

## Low-Stock Sourcing

`LowStockReport` keeps the sourcing toggle in the URL as
`include_sourcing=true`; the query key includes that boolean so cached plain
low-stock rows and sourced low-stock rows stay separate. When enabled, the table
renders TrustedParts attribution, sourcing status banners, sourcing columns, and
a per-row draft-PO action that reuses `CreateOrderLineModal` with the row's best
offer as its source. Source: `web/src/routes/reports/Reports.tsx`.

## BOM Buyability

`web/src/routes/reports/BomBuyabilityReport.tsx` reads `build_quantity` from the
URL, defaults invalid or missing values to `1`, and fetches
`GET /api/reports/bom-buyability?build_quantity=<n>` through
`web/src/lib/api.ts`.

The page uses the shared `DataTable`, surfaces the top-level sourcing status,
displays `PoweredByTrustedParts` and `SourcingSourceLabel`, and shows a
truncation badge when the backend returns `truncated: true`.

Rows link to each project's Source-BOM deep dive at
`/projects/:projectId/sourcing`. The reports tab and app route are registered in
`web/src/routes/reports/Reports.tsx` and `web/src/App.tsx`.

## Sourcing Risk

The sourcing-risk page uses TanStack Query with
`useWsKey("report", "sourcing-risk", onlyWithFlags)`, renders the shared
`DataTable`, and keeps the default list sorted by flag count before handing rows
to the table. The page uses `PoweredByTrustedParts` and
`SourcingSourceLabel` for TrustedParts attribution.

The page renders a status banner from `data.sourcing_status` instead of relying
on thrown API errors for normal provider states. The "Show only flagged"
checkbox maps directly to the API `only_with_flags` query parameter.
