# Reports Domain

Audience: engineer

Read-only aggregate services for workspace reports over inventory, stock, BOM,
and sourcing data.

- Routes live in `backend/app/api/routes/reports.py`.
- API docs live in `docs/api/reports.md`.
- Frontend docs live in `docs/frontend/reports.md`.
- Shared invariants are the workspace-isolation and stock-ledger rules in `docs/ARCHITECTURE.md`.
- Stock quantities must continue to flow through `backend/app/domain/stock/service.py`.
- Sourcing-backed reports use the short-lived TrustedParts cache and must not add persistent price-history storage.
