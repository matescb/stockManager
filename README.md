<div align="center">

# Stock Manager

**Self-hosted parts inventory & production management for electronics shops.**

Track parts → bag-scan into stock → consume against BOMs → ship builds. Every quantity change is an append-only ledger row, so nothing is ever silently overwritten.

[**Live: parts.matescb.cz →**](https://parts.matescb.cz/)
&nbsp;·&nbsp;
[Documentation](docs/)
&nbsp;·&nbsp;
[Changelog](CHANGELOG.md)
&nbsp;·&nbsp;
[Issues](https://github.com/matescb/stockManager/issues)

<br />

[![CI](https://img.shields.io/github/actions/workflow/status/matescb/stockManager/ci.yml?branch=main&label=CI&style=flat-square&logo=githubactions&logoColor=white)](https://github.com/matescb/stockManager/actions/workflows/ci.yml)
[![Production](https://img.shields.io/website?url=https%3A%2F%2Fparts.matescb.cz%2Fapi%2Fhealth&label=parts.matescb.cz&style=flat-square&up_message=healthy&down_message=down&up_color=brightgreen&down_color=red)](https://parts.matescb.cz/api/health)
[![Auto-deploy](https://img.shields.io/badge/deploy-auto%20on%20main-blue?style=flat-square&logo=githubactions&logoColor=white)](docs/deployment.md)
[![Last commit](https://img.shields.io/github/last-commit/matescb/stockManager/main?style=flat-square&logo=git&logoColor=white)](https://github.com/matescb/stockManager/commits/main)

<br />

[![Python](https://img.shields.io/badge/python-3.14-3776AB?style=flat-square&logo=python&logoColor=white)](backend/Dockerfile)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)](backend/app/main.py)
[![Postgres](https://img.shields.io/badge/Postgres-16-336791?style=flat-square&logo=postgresql&logoColor=white)](backend/alembic/)
[![Alembic](https://img.shields.io/badge/Alembic-33%20migrations-6f42c1?style=flat-square)](backend/alembic/versions/)
[![Node](https://img.shields.io/badge/node-25-339933?style=flat-square&logo=nodedotjs&logoColor=white)](web/Dockerfile.prod)
[![React](https://img.shields.io/badge/React-18.3-61DAFB?style=flat-square&logo=react&logoColor=black)](web/package.json)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.6-3178C6?style=flat-square&logo=typescript&logoColor=white)](web/tsconfig.json)
[![Vite](https://img.shields.io/badge/Vite-5.4-646CFF?style=flat-square&logo=vite&logoColor=white)](web/vite.config.ts)
[![TanStack Query](https://img.shields.io/badge/TanStack%20Query-5-FF4154?style=flat-square&logo=reactquery&logoColor=white)](web/src/lib/queryKeys.ts)
[![Tailwind](https://img.shields.io/badge/Tailwind-3-06B6D4?style=flat-square&logo=tailwindcss&logoColor=white)](web/tailwind.config.js)
[![Sentry](https://img.shields.io/badge/Sentry-wired-362D59?style=flat-square&logo=sentry&logoColor=white)](docs/phases/12-observability-sentry.md)

</div>

---

## Why this exists

Off-the-shelf inventory tools assume a warehouse. Electronics shops don't have a warehouse — they have **bags of components, reels on shelves, half-built boards on a desk**, and a mismatch between the thing on the bench (a 100-piece reel of `LM358`) and the thing in the BOM (a substitute `MC1458`). Spreadsheets handle this for a while, then they don't.

Stock Manager is the smallest tool that handles the parts shop's actual workflow:

- **Bags scan in** as the operator unpacks a delivery (MIL-STD-130N / ANSI MH10.8.2 Data Matrix → `mpn`, `qty`, `lot`, `date code` extracted automatically; re-scanning a known bag matches a stable signature).
- **Stock is a ledger.** Adds, removes, moves, builds, and corrections are append-only rows tagged with their cause (`order_id`, `build_id`, `related_entry_id` for moves). There is no `inventory.qty` column — current stock is computed from the ledger.
- **BOMs and builds are first-class.** Build a project's BOM, see shortages with substitute fallback, and consume in one transaction that locks every part up front so concurrent builds can't double-spend the same reel.
- **Workspaces are isolated** in code (every query filters by `workspace_id`), so multiple teams or projects share the deploy without leaking.
- **Multi-source providers.** DigiKey and Mouser plug in for MPN lookup, parametric specs, datasheets, and live availability. Provider credentials are encrypted per-workspace.

## At a glance

| | |
|---|---|
| **Stack** | FastAPI · SQLAlchemy 2 · Alembic · Postgres 16 · React 18 · TypeScript · TanStack Query · Vite · Tailwind |
| **Domains** | parts · stock (ledger) · lots · storage · projects · BOM · orders · builds · reports · workspaces · RBAC · audit · attachments · tags · custom_fields |
| **Routes** | 24 routers across `/api/*`, public `/catalog/*` |
| **Migrations** | 33 alembic versions, single-head enforced in CI |
| **Tests** | pytest (backend, with real Postgres) · vitest (frontend) · Playwright (e2e smoke) |
| **Deploy** | single VPS · Docker Compose · Apache + certbot · auto-deploy from `main` (gated by required reviewer) |
| **Backups** | nightly `pg_dump` + assets, age-encrypted, off-host to NAS, GFS retention (daily/weekly/monthly), pre-deploy snapshot |
| **Observability** | Sentry on backend + frontend; Sentry tunnel route; sourcemaps in CI; healthchecks.io dead-man's switch on the backup heartbeat |

## Quickstart

```bash
cp .env.example .env       # first time only — set SESSION_SECRET
make dev-up                # http://localhost:5173, API at http://localhost:8000/api
```

The backend container runs `alembic upgrade head` before uvicorn — no manual migrate step. The web container runs `vite --reload` against the API.

```bash
docker compose -f docker-compose.dev.yml exec backend pytest        # backend tests
cd web && npm test                                                   # frontend tests
cd web && npx playwright test                                        # e2e smoke
```

For local-host pytest (no Docker), see [`docs/development.md`](docs/development.md).

## Documentation

The repo's docs are organised as **shelves by audience** — start with [`docs/README.md`](docs/README.md) for the audience map.

| Shelf | For | Content |
|---|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Engineers (cold-start) | Stack, repo layout, ledger model, workspace isolation, API envelope, polymorphic tables. The single most load-bearing file. |
| [`docs/api/`](docs/api/) | Engineers | Per-router REST reference (15 areas) + envelope, error model, auth, pagination, rate-limit. |
| [`docs/domain/`](docs/domain/) | Engineers | Entity reference, ER diagram, ledger semantics, workspace-isolation rule, BOM/build/orders/providers/scan-import. |
| [`docs/frontend/`](docs/frontend/) | Engineers | Routing, `lib/api`, TanStack Query patterns, components, scanner, testing. |
| [`docs/adr/`](docs/adr/) | Engineers | 18 architecture decision records — every "Hard invariant" and "Things that have bitten us" rule has its own ADR. |
| [`docs/phases/`](docs/phases/) | Engineers | Per-feature rationale (Phases 1–13). |
| [`docs/development.md`](docs/development.md) | Engineers | Local dev loop, tests, lint baselines, migration workflow. |
| [`docs/deployment.md`](docs/deployment.md) | Ops | Prod architecture, CI/CD pipeline, ops, backups. |
| [`docs/runbooks/`](docs/runbooks/) | On-call | 10 ops scenarios — backup/restore, prod rollback, migration recovery, Sentry triage, incident response, SMTP/provider/workspace recovery, secret rotation. |
| [`docs/user/`](docs/user/) | End users | Task-shaped help — first-run, scan-import, BOM, builds, reports, workspace management. |
| [`CHANGELOG.md`](CHANGELOG.md) | Everyone | Release notes; phases 11–13 expand the buckets. |

## Architecture at a glance

```
┌─────────────────────────────────────────────────────────────────┐
│  Apache 2.4  (TLS via certbot)                                  │
│      │                                                          │
│      ├──→ /              → web container  (nginx + Vite build) │
│      └──→ /api/* + /catalog/*  → backend container (FastAPI)   │
│                                          │                      │
│                                          ▼                      │
│                              Postgres 16 (single instance)      │
│                                                                 │
│  Sidecars:                                                      │
│    backend-init   one-shot chown of /data (uploads volume)      │
│    vps-backup     nightly pg_dump + assets → age → NAS (GFS)    │
└─────────────────────────────────────────────────────────────────┘
```

Single-VPS, single-uvicorn-worker (slowapi rate-limit correctness — see [ADR-0012](docs/adr/0012-uvicorn-single-worker-slowapi.md)). Migrations apply on backend container start. Auto-deploy from `main` is gated by a required GitHub Environment reviewer; `production` approval triggers `git reset --hard origin/main` + `docker compose up -d --build` on the VPS.

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`CLAUDE.md`](CLAUDE.md) before opening a PR. The "Hard invariants" and "Things that have bitten us" sections of `CLAUDE.md` are not style preferences — they are the rules each ADR codifies.

CI gates: ruff (Python) + eslint (JS/TS) **delta-blocking** vs the checked-in baselines (`.ruff-baseline.txt`, `.eslint-baseline.txt`); `tsc -b` + `vite build`; pytest with a real Postgres service container; vitest; Playwright e2e; pip-audit + npm-audit; lockfile-drift; line-count-budget; ci-policy meta-gate.

## License

License not yet declared. Treat the contents of this repository as **all rights reserved** until a `LICENSE` file lands. If you want to use, fork, or redistribute Stock Manager, [open an issue](https://github.com/matescb/stockManager/issues) and we'll talk.
