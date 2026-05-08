# Reports Domain

Read-only report services over workspace-owned inventory, stock, BOM, and sourcing data.

Report routes live in `backend/app/api/routes/reports.py`. Shared invariants are the workspace-isolation and stock-ledger rules in `docs/ARCHITECTURE.md`; sourcing-risk stock quantities go through `domain/stock/service.py::bulk_current_quantities`.

