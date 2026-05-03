# ADR-0002: Code-enforced workspace isolation

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

The app is multi-tenant: every row of every domain table belongs to a workspace, and operators of workspace A must never read or write rows of workspace B. Postgres has row-level security (RLS) for exactly this — but RLS requires every connection to set a session variable per request, every migration to remember to enable RLS on new tables, and every test to either mock that out or run with elevated `BYPASSRLS`. Violations are silent until they're not.

The chosen alternative is to enforce isolation in service-layer code: every query filters by `workspace_id`, every cross-table FK lookup is followed by a `workspace_id` equality check, and the gate is a test file (`tests/test_workspace_isolation.py`) that any new endpoint must extend.

## Decision

Workspace isolation is enforced in service code, not by the database. There is no row-level security. Every query in every service filters by `ws.id`; every cross-table FK lookup is followed by a `workspace_id` equality check (helper: `_belongs(obj, workspace_id)` in `backend/app/domain/stock/service.py:46`).

The single exception is `parts.default_storage_location_id`, which is additionally guarded by a Postgres BEFORE trigger (`check_default_storage_workspace`, migration `0036_parts_default_storage_ws_trigger.py`). The trigger exists because the value can be set by a migration or admin SQL, where the service layer is bypassed; user-facing writes are still gated by the service check.

## Consequences

- **Good**: Tests can run against a plain Postgres role without `BYPASSRLS` gymnastics. Service code is the only place to look for "is this safe?". Cross-tenant joins fail loudly because the explicit `WHERE workspace_id = …` clause is missing.
- **Trade-offs**: A new endpoint that forgets the filter is a tenant-leak bug. Mitigation is `tests/test_workspace_isolation.py` — any new resource must add an isolation case there. The trigger on `parts.default_storage_location_id` is the only place where the policy "DB enforces isolation" leaks; adding more triggers would erode the rule.
- **What it forbids**:
  - Don't introduce row-level security on any table.
  - Don't write a query that touches a workspace-scoped table without `WHERE workspace_id = :ws_id` (or an equivalent join condition).
  - Don't trust an FK lookup result without the `_belongs` / `workspace_id ==` follow-up check — the FK only proves the row exists, not that it belongs to the caller's workspace.
  - Don't add a new endpoint that reads or writes workspace-scoped data without an isolation test in `tests/test_workspace_isolation.py`.

## Alternatives considered

- **Postgres row-level security (RLS)** — rejected because it requires per-request session variables, opt-in on every new table, and complicates tests and migrations. Silent failures (forgetting to enable RLS on a new table) are harder to detect than missing `WHERE` clauses, which show up immediately in code review.
- **An ORM-level "workspace-scoped" base query that auto-injects the filter** — rejected because it makes the filter implicit. When isolation is invisible, reviewers stop checking for it. Explicit `WHERE workspace_id = …` keeps every query auditable by grep.

## References

- Source: `backend/app/domain/stock/service.py:46-47` (`_belongs` helper)
- Source: `backend/alembic/versions/0036_parts_default_storage_ws_trigger.py`
- Source: `backend/tests/test_workspace_isolation.py`
- Rule: `CLAUDE.md:92-98`
- Architecture: `docs/ARCHITECTURE.md` — workspace isolation
