# Parts Inventory & Production Manager

Self-hosted web app for electronics parts, storage, stock, lots, BOMs and projects.

Live: <https://parts.matescb.cz/>. `main` auto-deploys via GitHub Actions —
see [`docs/deployment.md`](docs/deployment.md) for the full pipeline.

Implemented phases:

- **1–3** auth + workspaces; parts/storage with the append-only stock ledger; lots; projects with full CSV BOM import.
- **4** purchase orders + line-level receive (creates `source_type='purchase'` lots, ledger rows tagged `order_id`/`order_entry_id`). See `docs/phases/04-orders.md`.
- **5** builds + consume-from-BOM with substitute fallback, optional sub-assembly output lot. See `docs/phases/05-builds.md`.
- **6** read-only reports: low-stock, stock-value (by currency), BOM shortage, expiring lots. See `docs/phases/06-reports.md`.
- **7** BOM import presets — save / recall column mappings in the import wizard. See `docs/phases/07-bom-presets.md`.
- **8** meta-part members — manage `PartMetaMember` rows; build engine pulls stock from any member when consuming a meta-part BOM line. See `docs/phases/08-meta-parts.md`.
- **9** serial tracking — workspace + per-part flags enforce qty=1 + `serial_number` on add-stock and order-receive. See `docs/phases/09-serial-tracking.md`.
- **10** RBAC (owner/admin/member/viewer) + workspace invitations with token-based accept. See `docs/phases/10-rbac-invitations.md`.

Post-Phase-10 work (provider expansion, scan-to-import, switchable scanner
backend, security remediation, …) is recorded per-commit in
[`CHANGELOG.md`](CHANGELOG.md) rather than in additional phase docs —
the per-phase doc model retired with Phase 10.

The webcam barcode scanner lives at `/parts/scan-import` (a bulk
scan-then-import flow built on a switchable ZXing/Scandit backend in
`web/src/components/scanner/`). The legacy single-MPN lookup at
`/parts/scan` redirects there.

## Run locally

```bash
cp .env.example .env
docker compose up --build
```

- Web UI: <http://localhost:5173>
- API:    <http://localhost:8000/api>

Migrations run automatically when the backend container starts.

## Tests

```bash
docker compose exec backend pytest
```

For running tests outside Docker, see [`docs/development.md`](docs/development.md).

## Production

A push to `main` runs the test suite, then SSHes into the VPS and rebuilds
the docker-compose stack. The VPS's Apache 2.4 fronts everything and certbot
handles TLS. See [`docs/deployment.md`](docs/deployment.md) for the full
flow: architecture, one-time bootstrap, CI/CD details, day-to-day ops, and
backups.

## Layout

- `backend/` — FastAPI + SQLAlchemy 2 + Alembic
- `web/` — Vite + React + TypeScript + Tailwind. Scanner backends in
  `web/src/components/scanner/`; ZXing wasm + Scandit JS+wasm copied
  from `node_modules` to `public/` at build time.
- `docs/` — [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) (the cold-start doc), [`development.md`](docs/development.md), per-phase notes (1–10) under `docs/phases/`
