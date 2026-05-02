# Architecture

This document is for someone landing in the codebase cold. It explains
how the pieces fit together; per-phase docs (`docs/phases/`) explain
what each feature does.

## Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2 (Declarative + Core),
  Alembic, psycopg 3, Pydantic 2 / pydantic-settings, argon2-cffi,
  itsdangerous, chardet.
- **Database**: Postgres 16. The schema uses Postgres-specific types
  (`UUID`, `ARRAY`, `Numeric`); SQLite is not a viable substitute.
- **Frontend**: Vite 5 + React 18 + TypeScript 5 + Tailwind 3 +
  TanStack Query 5 + react-router 6 + react-hook-form + zod.
- **Auth**: opaque session token in a httpOnly cookie; argon2 password
  hashes; sessions persisted in `user_sessions`.

### Sync, not async

Routes are synchronous (`def`, not `async def`). SQLAlchemy is
configured for the sync API — there is no `AsyncSession`, no
`asyncpg`, no `await db.query(...)`. FastAPI is async-native and the
runtime handles a sync handler by running it on a worker thread, which
is the right trade for a Postgres-bound app: SQLAlchemy's sync API is
mature, transaction handling stays straightforward, and the workload
is dominated by DB round-trips that already release the GIL.

Use `async def` only when you need to consume an async-only library.
Today the backend has exactly five `async def`s and they're all
deliberate:

- `app/api/routes/sentry_tunnel.py::sentry_tunnel` — forwards a Sentry
  envelope upstream via `httpx.AsyncClient`; needs `await
  request.stream()` to enforce the body cap chunk-by-chunk.
- `app/api/routes/attachments.py::upload` — streams `UploadFile.read()`
  for content-addressed asset writes.
- `app/main.py::*Middleware.dispatch` — request-logging middleware;
  Starlette middleware contract is async.
- `app/core/responses.py::http_exception_handler` /
  `validation_exception_handler` — FastAPI exception handlers are
  defined as async.

If you find yourself wanting to add a sixth, double-check: never
`await db.query(...)` against the sync session — it'll either crash or
silently block the event loop. If a route really does need to consume
an async library, keep DB access in a sync helper and call it through
`anyio.to_thread.run_sync` or split the handler so the sync section
stays sync.

## Repo layout

```
backend/
  app/
    api/routes/         FastAPI routers, one per resource
    core/               config, deps, auth helpers, response shape
    domain/<bounded>/   models.py, schemas.py, service.py
    infra/db.py         Base, engine, SessionLocal, get_db()
    main.py             FastAPI app, router include
  alembic/              migrations (one per phase)
  tests/                pytest, integration-flavoured (real Postgres)

web/
  src/
    components/         shared UI (DataTable, EntityHeader, SubNav, …)
    components/layout/  AppShell — global nav, search, workspace switcher
    lib/api.ts          minimal fetch wrapper (credentials: "include")
    lib/auth.tsx        AuthContext + Gate
    routes/<area>/      one folder per domain (parts, orders, builds, …)
    types.ts            shared TS types matching backend serializers
    App.tsx             react-router config
docs/
  ARCHITECTURE.md       this file
  development.md        local-test setup
  phases/NN-<name>.md   per-phase rationale + API + UI + tests
```

## The ledger model

Stock is **append-only**. `stock_entries` is a sequential log; the
"current quantity" of any (part, lot, storage) tuple is always
`SUM(quantity_delta)` over the matching rows with `status='on_hand'`.

There is no `inventory.qty` column anywhere — all quantity reads go
through `domain/stock/service.py::current_quantity` (or one of the
roll-ups built on top of it).

### Why this matters

- Add stock → one positive row.
- Remove stock → one negative row.
- Move stock → one negative row at the source + one positive row at
  the destination, linked by `related_entry_id`.
- Adjust count → one row whose delta is `actual_qty − current_qty`.
- Receive a PO line → one positive row with `operation_type='receive'`,
  `order_id` and `order_entry_id` set, and a fresh `Lot` with
  `source_type='purchase'`.
- Build consume → one negative row per consumed line with
  `operation_type='build_consume'` and `build_id` set; if the project
  has an associated sub-assembly, an extra positive row with
  `operation_type='build_produce'` and a `Lot` with
  `source_type='build'`.

Operations are the unit of audit. Reading the full ledger for a part
gives the entire stock history; aggregating the ledger gives any
current view.

### Integer-only quantities (DB-005 / migration 0030)

All quantity fields throughout the system are integers — `stock_entries.
quantity_delta`, `project_entries.quantity`, and `order_entries.
quantity_ordered` / `quantity_received`. This matches the electronics
domain (no fractional component counts are needed) and eliminates the
precision-loss path that existed when `project_entries.quantity` was
`Numeric(18,6)` while the ledger column was `Integer`. BOM imports that
contain fractional quantity values are rejected at the API layer with a
422 before any rows are written.

### Lot lifecycle

A `Lot` is a *batch* of a part with a particular provenance:

| `source_type` | Created by | Notes |
|---------------|------------|-------|
| `manual`      | Add-stock with no order context | Optional |
| `split`       | Move-stock with `split_lot=true` | `parent_lot_id` set |
| `purchase`    | Order receive | `source_order_id` set |
| `build`       | Build consume that produces an output sub-assembly | `source_build_id` set |

`Lot.serial_number` carries the per-unit serial when the workspace +
part have serial tracking on (Phase 9).

## Workspace isolation

Every domain table inherits the `WorkspaceOwned` mixin (in
`domain/_mixins.py`):

```python
id, workspace_id (FK CASCADE), created_at, updated_at,
created_by/updated_by (FK SET NULL on users), archived_at
```

`workspace_id` is enforced at the **service layer**: every query
filters by `ws.id`, and every cross-table FK lookup is followed by a
`workspace_id` equality check. There is no row-level security in the
database — the protection is the consistent code pattern. The
`tests/test_workspace_isolation.py` test pins this contract for the
parts router; new endpoints should add equivalent coverage.

`get_current_workspace()` reads the workspace from the
`X-Workspace-Id` header or the `stockmgr_workspace` cookie, validates
membership, and falls back to the user's first active membership.

## API conventions

Every JSON response wraps the payload in `{ data, status }`:

```json
{ "data": { … }, "status": { "category": "ok", "message": "OK" } }
```

`category` is `ok` for 2xx and otherwise one of:
`unauthenticated | forbidden | not_found | conflict | validation_error | server_error`.

The frontend's `lib/api.ts` unwraps `data` automatically and throws
`ApiError(status, body, msg)` on non-2xx. Helpers `responses.ok()` /
`responses.err()` produce these envelopes server-side. The two
`add_exception_handler` calls in `main.py` translate FastAPI's
`HTTPException` and Pydantic `ValidationError` into them.

`responses.ok()` is generic over the payload type — annotate a route's
return as `Envelope[PartOut]` (or `Envelope[list[PartOut]]`,
`Envelope[None]`, …) to propagate the inner shape through static type
checking. The runtime payload is still a plain dict; the `Envelope[T]`
type alias is a `TypedDict[Generic[T]]` so error paths can spread
extra keys (e.g. `existing_id` on a 409) onto the top level without
tripping any schema strictness. CQ-007 (#123).

## Domain decomposition

| Domain | Tables | Service / endpoints |
|--------|--------|---------------------|
| users + workspaces | users, user_sessions, workspaces, workspace_members | auth (signup/login/logout/me), workspaces |
| parts | parts, part_cad_keys, part_meta_members, part_substitutes | parts (CRUD, archive, scan, substitutes, members) |
| storage | storage_locations | storage (CRUD, archive, history) |
| stock | stock_entries (ledger), lots | stock (add/remove/move/adjust/history), lots |
| projects | projects, project_entries, bom_import_presets | projects (CRUD, BOM CRUD + import wizard), bom_presets |
| orders | orders, order_entries | orders (CRUD, archive, receive) |
| builds | builds | builds (CRUD, consume) |
| cross-cutting | attachments, custom_fields, tags, tag_links | attachments, custom_fields, tags |
| reports | (read-only over the above) | reports (low-stock, stock-value, bom-shortage, expiring-lots) |

Each domain folder contains:
- `models.py` — SQLAlchemy declarative classes
- `schemas.py` — Pydantic request/response DTOs. **Rule (CQ-006 / #122):
  every domain has its `schemas.py`; routers must not declare inline
  `class XxxIn(BaseModel)` blocks.** This is the single source of truth
  so shared shapes can be lifted across routers without an import cycle
  through `app.api.routes.*`. Small domains may have an empty file; the
  file itself is the "yes, this is where schemas live" signal.
- `service.py` — pure DB-touching logic that the route layer wraps in
  HTTPException-mapping try/excepts *(only for non-trivial flows like stock, orders, builds)*

## Migrations

`backend/alembic/versions/` holds a linear chain of revisions named
`NNNN_short_description.py`. Each file has a stable four-digit revision
ID (`'0001'`, `'0002'`, …) and a `down_revision` pointing at the
previous one. Through `0005` the chain corresponded 1:1 with
`docs/phases/` per-phase docs; from `0006` onward migrations land
per-feature with `CHANGELOG.md` as the canonical record. The full table
of revisions is in `docs/development.md`.

The first migration is autogenerated from the full Phase 1–3 schema
and uses an explicit `op.create_foreign_key(use_alter=True)` after the
last `create_table` to set up the `parts ↔ projects` cycle. Each
later migration is a delta autogenerated against the metadata at the
time, then renamed and reviewed.

**Don't edit a migration file in place once it's on `main`.** It has
already been auto-deployed to prod (the backend container's CMD runs
`alembic upgrade head` on every start), and editing breaks the chain
on the next deploy. Add a new migration instead. The pre-edit hook in
`.claude/hooks/pre-edit-migration-guard.sh` enforces this for Claude
Code sessions.

To autogenerate a new revision after a model change:

```bash
DATABASE_URL=… alembic revision --autogenerate -m "what changed"
```

Review carefully — alembic's autogen has known gaps:

- `use_alter` FKs are emitted as a separate `op.create_foreign_key` at
  the end of `upgrade()`, not as part of the `create_table`. If a new
  cycle appears, you may need to add this manually.
- Adding a NOT NULL column to a non-empty table needs a
  `server_default` (and an `op.alter_column(server_default=None)`
  after — see `0004_part_serialized.py` for the pattern).
- New tables use the autogen-chosen name suffix; rename to
  `NNNN_<descriptive>.py`.

## Frontend conventions

- All requests go through `api.{get|post|patch|delete|upload}` in
  `lib/api.ts`. It sets `credentials: "include"` so the session cookie
  rides along automatically.
- Server state is owned by TanStack Query; queryKeys follow the form
  `[<resource>, <id?>, <sub?>]`. Mutations invalidate by key prefix.
- Routing is plain react-router with a top-level `<Gate>` that hands
  the user off to `/login` if `useAuth().me` is null.
- `DataTable` (in `components/`) provides search-filter, column
  sorting, hidden columns, and CSV export. New list pages should
  reach for it before rolling their own table.
- Tailwind + a tiny set of utility classes (`btn`, `btn-primary`,
  `btn-danger`, `card`, `pill`, `input`, `label`, `table`) keep the
  visual language consistent — those classes are defined in
  `src/index.css`.

## Auth and sessions

`/api/auth/signup` creates the user, a personal workspace, and a
membership in one transaction; then issues a session by writing a
`user_sessions` row and setting an httpOnly cookie. `get_current_user`
validates the cookie's token and the row's `expires_at` on every
request. Logout deletes the row and clears the cookie.

RBAC arrived in Phase 10. `WorkspaceMember.role` is now one of
`{owner, admin, member, viewer}`. The `core/deps.py::require_role`
dependency factory enforces it on member-management and workspace-
settings endpoints. Mutating data endpoints (parts/stock/projects/…)
are not yet viewer-gated; that's a deliberate follow-up.

## Future work (not implemented)

- **User deletion** — there is no `DELETE /api/users/{id}` endpoint
  today. `workspaces.owner_user_id` carries `ondelete='RESTRICT'` (every
  other user-facing FK is `SET NULL` for audit columns or `CASCADE` for
  membership), so the workspace owner row cannot disappear without an
  ownership reassignment. When the endpoint lands it must call
  `app.domain.users.service.assert_user_deletable(db, user_id)` *before*
  issuing the SQL delete; the helper raises a structured 409 with
  `{ code: "owns_workspaces", workspaces: [...] }` so the caller can
  prompt for reassign-or-archive instead of letting a Postgres
  `ForeignKeyViolation` bubble into a 500. See
  `tests/test_user_deletion_guard.py` for the contract.
- **Per-endpoint RBAC tightening** — Phase 10 introduced roles and
  invitations and gates the member-management surface. Mutating data
  endpoints (parts/stock/projects/…) currently sit behind a single
  method-aware `require_member_for_writes` gate that disallows viewers
  from any non-GET. Per-endpoint role differentiation (e.g.
  receive-order is `member+` but archive-order is `admin+`) is the
  natural next step. Mechanical but sweeping; do it together with a
  router-level test pass.
- **Pagination** — most list endpoints return the full set with no
  `limit`/`offset`. `/api/parts` and `/api/stock` cap at 200/1000
  via a `Query(default=200, le=1000)`; the rest don't. A workspace
  with 50k lots will hang the lots page. Adding cursor-based pagination
  to every list endpoint is a non-trivial follow-up.
- **Reports** — costs are summed using `Lot.purchase_unit_cost`. A
  proper "weighted average cost" or "FIFO/LIFO valuation" report
  would be a real next step. Same for shortage-aware order-suggestion.
- **Stock concurrency** — stock writes do not yet hold row-level locks
  during the read-then-write window of `current_quantity → StockEntry`
  insertion. Two concurrent operators consuming from the same lot can
  both pass the availability check before either writes. The pending
  PR #6 in the comprehensive remediation plan adds advisory locks
  keyed on `(workspace, part)` and a database trigger preventing
  cumulative balances from going negative.
