# Project Sourcing Route

Feature-local route files for BOM sourcing and purchase-plan review. Broader
frontend conventions live in `docs/frontend/`; architecture rules live in
`docs/ARCHITECTURE.md`.

- `ProjectSourcingPage.tsx` owns route orchestration, modal state, and navigation.
- `useSourcingFilters.ts` owns workspace defaults, active-list warnings, and request shape.
- `useProjectSourcing.ts` owns the explicit Source mutation and display cache.
- `SourcingControls.tsx` owns build/country/currency/distributor inputs.
- `CapacityBanner.tsx` owns build capacity and sourcing cost summary.
- `CoverageMatrix.tsx` owns distributor coverage cards and matrix.
- `BomRows.tsx` owns the sourced BOM table and distributor drill-down entry.
- `SourcingStates.tsx` owns loading, empty, diagnostics, and retry states.
- `sourcingTypes.ts` and `sourcingHelpers.ts` hold shared route-local types and pure helpers.
- Modal and purchase-plan files remain adjacent because they share sourcing DTOs.
