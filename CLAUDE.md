# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Orientation

Read these in this order — they are the canonical source of truth and are
kept current:

- `docs/ARCHITECTURE.md` — cold-start doc: stack, repo layout, the ledger
  model, workspace isolation, API envelope, domain decomposition,
  migrations, frontend conventions.
- `docs/development.md` — local dev + how to run tests outside Docker.
- `docs/deployment.md` — prod architecture, CI/CD, ops, backups.
- `docs/phases/NN-*.md` — per-feature rationale (Phases 1–10).
- `CHANGELOG.md` — post-Phase-10 work that didn't get a phase doc
  (production hardening, Sentry, scan-to-import, providers, etc.).

The single most load-bearing file in this repo is `docs/ARCHITECTURE.md`.
Don't restate things from there in code or new docs — link to it.

## Commands

### Dev loop (Docker)

```bash
docker compose up --build       # http://localhost:5173, API at :8000/api
```

Backend container runs `alembic upgrade head` before uvicorn — no manual
migrate step in dev. Web container runs `vite --reload`.

### Tests

```bash
docker compose exec backend pytest               # all backend tests
docker compose exec backend pytest -k <name>     # single test by name
cd web && npm test                                # vitest (currently sparse)
```

Outside Docker, the backend test suite needs a real Postgres (no SQLite
fallback — schema uses `UUID`, `ARRAY`, `Numeric`):

```bash
TEST_DATABASE_URL=postgresql+psycopg://stockmgr:stockmgr@127.0.0.1:5432/stockmgr_test \
  python -m pytest -q
```

`tests/conftest.py` drops + recreates the public schema between tests.

### Build / lint

There is **no Python linter and no JS linter configured**. CI's only
static check is `tsc -b` (run as part of `npm run build`) and `pytest`.
Don't add tooling without asking.

### Migrations

```bash
DATABASE_URL=… alembic revision --autogenerate -m "what changed"
```

Then rename the generated file to `NNNN_short_name.py`, where `NNNN` is the
next two-digit (zero-padded) integer continuing the existing chain in
`backend/alembic/versions/`. The revision ID inside the file is the same
`NNNN` string. Review the autogen output — known gaps are listed in
`docs/development.md`.

**Don't edit a migration file once it's been merged to `main`** — it's
already been applied in prod by the auto-deploy. Add a new migration
instead.

## Hard invariants

These are easy to violate by accident; if your change would break one of
them, that's the bug.

- **No `inventory.qty` column.** Stock is an append-only ledger
  (`stock_entries`). All quantity reads go through
  `domain/stock/service.py::current_quantity` or roll-ups built on it.
  Never compute "current stock" by joining or aggregating outside that
  service.
- **Workspace isolation is enforced in code, not the DB.** Every query in
  every service filters by `ws.id`, and every cross-table FK lookup is
  followed by a `workspace_id` equality check. There is no row-level
  security. New endpoints must replicate this; pin it with a test in
  `tests/test_workspace_isolation.py` style.
- **API envelope.** Every response is `{ data, status }` — never a bare
  payload. Server-side use `responses.ok()` / `responses.err()`.
  Client-side `lib/api.ts` unwraps `data` and throws `ApiError(status,
  body, msg)` on non-2xx; that body is the dict from `HTTPException(detail=…)`,
  which `core/responses.py::http_exception_handler` spreads onto the
  response (e.g. 409 returns `{ existing_id, existing_name, … }`).
- **MPN uniqueness per workspace.** Partial unique index
  `uq_parts_ws_mpn` (`WHERE mpn IS NOT NULL AND archived_at IS NULL`).
  Create-part returns 409 with `existing_id` + `existing_name` on
  collision.
- **Content-addressed assets.** Provider images and datasheets are
  downloaded once, stored at `{UPLOAD_DIR}/parts/{ws_id}/{sha}.{ext}`,
  served via `GET /api/parts/assets/{ws_id}/{filename}` (optional `?name=`
  for the Save-As dialog). Don't change the URL structure — `PartInfo`
  builds it directly with `withDownloadName()`.
- **`bag_signature`** on `stock_entries` is the SHA-256 of the normalised
  raw bag code. Re-scanning a bag matches the same signature, which is
  how the inline "Found bag" UI works. If you touch
  `web/src/lib/bagCode.ts`, keep the normalisation order the same — the
  signature is the only stable correlation key.
- **Provider catalog vs spec keys.** `web/src/lib/providerCatalog.ts`
  defines which custom-field keys are catalog metadata (price, stock,
  manufacturer URL, …) vs user-curated specs. The PartSpecs and
  PartSourcing tabs split on this boundary; the same key list lives
  server-side in `backend/app/domain/parts/services/provider.py`.
  Adding a new catalog field needs both sides.

## Things that have bitten us — don't undo

- **`docker-compose.prod.yml` `command:` is a single-line JSON-array
  exec form.** YAML folded scalar (`>`) preserved newlines on indented
  continuation lines, so `--proxy-headers --forwarded-allow-ips=*`
  ran as a separate (failing) shell command. Result: slowapi bucketed
  every client by the docker bridge IP. If you reformat the compose
  file, keep the array form.
- **Session cookie `secure` is gated on `APP_ENV == "prod"`** in
  `backend/app/api/routes/auth.py::_set_session_cookie`. Don't make it
  unconditional — local dev runs over HTTP and the cookie wouldn't
  round-trip.
- **uvicorn runs `--workers 1`** in prod. slowapi's bucket store is
  per-process; bumping workers multiplies the effective rate limit.
  If traffic ever justifies more, switch slowapi to a Redis backend
  first.
- **`web/vite.config.js`** is auto-emitted by the composite TypeScript
  project — gitignored. Don't commit it.
- The repo has had transient `review-*` directories and stray venvs
  appear at the root in past sessions. They're gitignored; don't
  unignore them.

## Frontend conventions worth preserving

- All HTTP goes through `web/src/lib/api.ts` (`get` / `post` / `patch` /
  `delete` / `upload`) so the session cookie rides along
  (`credentials: "include"`) and `ApiError` is uniform.
- Server state lives in TanStack Query; query keys are
  `[<resource>, <id?>, <sub?>]`. Mutations invalidate by key prefix.
- `components/DataTable` does search, sort, hidden columns, CSV export,
  and now multi-select. Use it before rolling your own table.
- The visual language is a small Tailwind utility set defined in
  `src/index.css` (`btn`, `btn-primary`, `btn-danger`, `card`, `pill`,
  `input`, `label`, `table`). Use those before adding new ones.

## Deploy is automatic

A merge to `main` runs CI (backend pytest + web vitest + `tsc -b` + `vite
build`); on green, GitHub Actions SSHes the VPS, `git reset --hard
origin/main`, and `docker compose up -d --build`. Migrations apply on
backend container start. There is **no manual deploy step and no staging
environment.** Treat `main` accordingly: a destructive migration goes
straight to prod, so take a `pg_dump` first (see
`docs/deployment.md#backups`).

## Allow-listed external resource

`WebFetch(domain:www.trustedparts.com)` is allow-listed in
`.claude/settings.local.json` — it's the planned source for future
external part-attribute lookups by MPN. Mouser and DigiKey are the
currently shipped providers; their secrets live per-workspace, encrypted
at rest.
