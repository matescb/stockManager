# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Orientation

`docs/README.md` is the audience map — it routes engineers, on-call, and end
users to the right shelf. The tree below lists the canonical references in
the order a new engineer should read them.

**Cold-start (read in order):**

- `docs/ARCHITECTURE.md` — stack, repo layout, the ledger model, workspace
  isolation, API envelope, domain decomposition, migrations, frontend
  conventions. The single most load-bearing file in this repo. Don't
  restate things from there in code or new docs — link to it.
- `docs/development.md` — local dev + how to run tests outside Docker.
- `docs/deployment.md` — prod architecture, CI/CD, ops, backups.

**Reference shelves (engineer):**

- `docs/api/` — per-router REST reference (15 areas + envelope/error/auth
  conventions).
- `docs/domain/` — entity & data-model reference; `data-model.md` has the
  full ER diagram.
- `docs/frontend/` — frontend developer guide (routing, lib/api, TanStack
  patterns, components, scanner, testing).
- `docs/adr/` — architecture decision records. ADRs 0001–0017 are
  retro-documented and codify every "Hard invariant" and "Things that
  have bitten us" rule below; 0018 (prod SMTP fail-closed) is the
  first non-retro entry. When you find yourself about to undo one of
  those rules, read the ADR.
- `docs/phases/NN-*.md` — per-feature rationale (Phases 1–13). 1–3 are
  retro-documented from migrations; 11–13 cover post-Phase-10 work.
- `CHANGELOG.md` — release notes; phase docs 11–13 expand the buckets.
- In-tree module READMEs at `backend/app/domain/*/README.md`,
  `backend/app/{api/routes,core}/README.md`,
  `web/src/{lib,components,routes}/README.md` — short orientation,
  link out to `docs/`.

**Ops shelf (on-call):**

- `docs/runbooks/` — 10 runbooks: secret-rotation, backup-restore,
  prod-rollback, migration-recovery, sentry-triage, on-call-quickstart,
  incident-response, smtp-outage, provider-outage, workspace-recovery.
  `docs/runbooks/README.md` has the severity matrix.

**End-user shelf:**

- `docs/user/` — end-user help pages (audience: operator/warehouse staff).
  Engineer-only links should never point here, and vice versa.

**Doc-author guide:**

- `docs/STYLE.md` — every page in `docs/` conforms to it. Read before
  contributing.

## Commands

### Dev loop (Docker)

```bash
cp .env.example .env   # first time only — set SESSION_SECRET
make dev-up            # http://localhost:5173, API at :8000/api
```

Backend container runs `alembic upgrade head` before uvicorn — no manual
migrate step in dev. Web container runs `vite --reload`.

The dev compose file is `docker-compose.dev.yml`; the prod compose file is
`docker-compose.prod.yml`. The `Makefile` wraps both so neither is the
implicit default (avoids running the dev stack on prod by muscle memory).

### Tests

```bash
docker compose -f docker-compose.dev.yml exec backend pytest               # all backend tests
docker compose -f docker-compose.dev.yml exec backend pytest -k <name>     # single test by name
cd web && npm test                                                           # vitest (currently sparse)
```

Outside Docker, the backend test suite needs a real Postgres (no SQLite
fallback — schema uses `UUID`, `ARRAY`, `Numeric`). `tests/conftest.py`
defaults `DATABASE_URL` to
`postgresql+psycopg://stockmgr:stockmgr@127.0.0.1:5432/stockmgr_test`, so
with a local Postgres listening on `127.0.0.1:5432` and the `stockmgr` user
(password `stockmgr`, `CREATEDB` privilege) you can simply run:

```bash
cd backend && python -m pytest -q
```

The test DB (`stockmgr_test`) is auto-created by conftest if it doesn't
exist. For a non-default host/port/credentials, set `TEST_DATABASE_URL`:

```bash
TEST_DATABASE_URL=postgresql+psycopg://stockmgr:stockmgr@127.0.0.1:5432/stockmgr_test \
  python -m pytest -q
```

Note: the dev compose file does not publish Postgres to the host, so host
pytest needs a separately installed local Postgres (not the Docker one).
`tests/conftest.py` drops + recreates the public schema between tests.

### Build / lint

CI runs both `ruff` (Python) and `eslint` (JS/TS) as **baseline-blocking**
gates — they fail only on violations that are NEW relative to the
checked-in baselines (`.ruff-baseline.txt` and `.eslint-baseline.txt`).
`tsc -b` (via `npm run build`) and `pytest` are the other static checks.
To update the baselines after intentional cleanup, see
`docs/development.md` — "Updating lint baselines".

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
- **Workspace isolation is enforced in code, not the DB** — except
  `parts.default_storage_location_id`, which is additionally enforced by a
  Postgres BEFORE trigger (`parts_default_storage_workspace_check`, migration
  0036). Every query in every service filters by `ws.id`, and every
  cross-table FK lookup is followed by a `workspace_id` equality check. There
  is no row-level security. New endpoints must replicate this; pin it with a
  test in `tests/test_workspace_isolation.py` style.
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
- **No `verify=False` on httpx clients.** CI greps for `verify=False`,
  `trust_env=False`, `ssl=False` under `backend/app/`. Annotate with
  `# noqa: tls-verify` if intentional (e.g. internal test doubles).

## Things that have bitten us — don't undo

- **`docker-compose.prod.yml` `command:` is a single-line JSON-array
  exec form.** YAML folded scalar (`>`) preserved newlines on indented
  continuation lines, so `--proxy-headers --forwarded-allow-ips=*`
  ran as a separate (failing) shell command. Result: slowapi bucketed
  every client by the docker bridge IP. If you reformat the compose
  file, keep the array form.
- **`backend-init` one-shot service handles `chown /data` before `backend`
  starts.** The backend Dockerfile sets `USER appuser` (UID 1000); the
  runtime container has no root privileges. Ownership of the `uploads`
  named volume is fixed by `backend-init` (`restart: no`,
  `command: ["sh","-c","chown -R 1000:1000 /data"]`) which runs as root
  and exits cleanly. `backend` declares
  `depends_on: backend-init: condition: service_completed_successfully`
  so it waits for a clean exit. Don't reintroduce gosu or a root-prefixed
  `command:` in the backend service — that was the pattern this replaced.
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
- **`--timeout-graceful-shutdown` must be less than `stop_grace_period`.**
  The backend uvicorn command uses `--timeout-graceful-shutdown 25` and the
  service has `stop_grace_period: 30s`. Keep the uvicorn value at least 5s
  below the compose value so Compose's SIGKILL never fires during a clean
  drain (INFRA2-014).
- The repo has had transient `review-*` directories and stray venvs
  appear at the root in past sessions. They're gitignored; don't
  unignore them.
- **Sentry auth token must not enter the Docker build context.** Source-map
  upload is handled by the CI `web-build` job (`npx @sentry/cli sourcemaps
  upload`) after `npm run build`, gated on push to `main`. `SENTRY_AUTH_TOKEN`
  / `SENTRY_ORG` / `SENTRY_PROJECT` are GitHub Actions secrets — do **not**
  add them back to `web/Dockerfile.prod` ARG/ENV or `docker-compose.prod.yml`
  build args (INFRA2-010).
- **Base images are digest-pinned (INFRA2-015).** `backend/Dockerfile` and
  `web/Dockerfile.prod` use `FROM image@sha256:<digest>` — do **not** loosen
  to a bare tag. Digests are rotated by Dependabot weekly (`.github/dependabot.yml`).
  To bump manually: `curl -s https://registry.hub.docker.com/v2/repositories/library/<image>/tags/<tag>
  | python3 -c "import sys,json; print(json.load(sys.stdin)['digest'])"`,
  then update the `@sha256:` line and the `# Digest pinned on` comment.
- **Sourcemaps are only emitted in CI.** `web/vite.config.ts` gates
  `build.sourcemap` on `SENTRY_AUTH_TOKEN` presence (INFRA2-015). VPS builds
  without the token produce no `.map` files, so the Docker build cache is
  clean. The `find -name '*.map' -delete` in `web/Dockerfile.prod` remains
  as belt-and-braces for edge-case local builds.
- **"Step 1 of N" PRs must not close the parent issue.** Using `Closes #N`
  on a partial PR auto-closes the issue the moment the PR merges, leaving the
  remaining steps with no tracking. Use `Refs #N` instead, or file a follow-up
  issue and link it. Full rule in `CONTRIBUTING.md` (multi-step issues rule).

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

## Deploy is automatic — but gated by a human reviewer

A merge to `main` runs CI (backend pytest + web vitest + `tsc -b` + `vite
build`); on green, the `deploy` job **pauses for a required human reviewer**
(GitHub Settings → Environments → `production` → Required reviewers). After
approval, GitHub Actions SSHes the VPS, `git reset --hard origin/main`, and
`docker compose up -d --build`. Migrations apply on backend container start.

There is **no staging environment.** Treat `main` accordingly: a destructive
migration goes straight to prod, so take a `pg_dump` first (see
`docs/deployment.md#backups`).

## Allow-listed external resource

`WebFetch(domain:www.trustedparts.com)` is allow-listed in
`.claude/settings.local.json` — it's the planned source for future
external part-attribute lookups by MPN. Mouser and DigiKey are the
currently shipped providers; their secrets live per-workspace, encrypted
at rest.
