# Reports Frontend

Audience: engineer

Frontend report routes and their data-fetching conventions.

## Routes

| Route | Component | API |
|---|---|---|
| `/reports` | `web/src/routes/reports/Reports.tsx` | `GET /api/reports/low-stock` |
| `/reports/sourcing-risk` | `web/src/routes/reports/SourcingRiskReport.tsx` | `GET /api/reports/sourcing-risk` |

## Low-Stock Sourcing

`LowStockReport` keeps the sourcing toggle in the URL as `include_sourcing=true`; the query key includes that boolean so cached plain low-stock rows and sourced low-stock rows stay separate. When enabled, the table renders TrustedParts attribution, sourcing status banners, sourcing columns, and a per-row draft-PO action that reuses `CreateOrderLineModal` with the row's best offer as its source. Source: `web/src/routes/reports/Reports.tsx`.

The sourcing-risk page uses TanStack Query with `useWsKey("report", "sourcing-risk", onlyWithFlags)`, renders the shared `DataTable`, and keeps the default list sorted by flag count before handing rows to the table. The page uses `PoweredByTrustedParts` and `SourcingSourceLabel` for TrustedParts attribution.

## Sourcing Risk

The page renders a status banner from `data.sourcing_status` instead of relying on thrown API errors for normal provider states. The "Show only flagged" checkbox maps directly to the API `only_with_flags` query parameter.

References:

- Route registration: `web/src/App.tsx:91-97`, `web/src/App.tsx:269-274`
- Navigation entry: `web/src/routes/reports/Reports.tsx:101-106`
- Component: `web/src/routes/reports/SourcingRiskReport.tsx:93-230`
- Tests: `web/src/routes/reports/__tests__/SourcingRiskReport.test.tsx:79-131`
