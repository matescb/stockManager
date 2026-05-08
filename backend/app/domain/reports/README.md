# Reports Domain

Read-only aggregate services for workspace reports.

- Routes live in `backend/app/api/routes/reports.py`.
- API docs live in `docs/api/reports.md`.
- Frontend docs live in `docs/frontend/reports.md`.
- Shared invariants are the workspace-isolation and stock-ledger rules in `docs/ARCHITECTURE.md`.
- Stock quantities must continue to flow through `backend/app/domain/stock/service.py`.
