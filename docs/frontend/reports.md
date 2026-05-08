# Reports Frontend

Audience: engineer

Frontend report routes and their data-fetching conventions.

## Routes

| Route | Component | API |
|---|---|---|
| `/reports/sourcing-risk` | `web/src/routes/reports/SourcingRiskReport.tsx` | `GET /api/reports/sourcing-risk` |

The sourcing-risk page uses TanStack Query with `useWsKey("report", "sourcing-risk", onlyWithFlags)`, renders the shared `DataTable`, and keeps the default list sorted by flag count before handing rows to the table. The page uses `PoweredByTrustedParts` and `SourcingSourceLabel` for TrustedParts attribution.

## Sourcing Risk

The page renders a status banner from `data.sourcing_status` instead of relying on thrown API errors for normal provider states. The "Show only flagged" checkbox maps directly to the API `only_with_flags` query parameter.

References:

- Route registration: `web/src/App.tsx:91-97`, `web/src/App.tsx:269-274`
- Navigation entry: `web/src/routes/reports/Reports.tsx:101-106`
- Component: `web/src/routes/reports/SourcingRiskReport.tsx:93-230`
- Tests: `web/src/routes/reports/__tests__/SourcingRiskReport.test.tsx:79-131`
