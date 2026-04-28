# Parts Inventory & Production Manager

Self-hosted web app for electronics parts, storage, stock, lots, BOMs and projects.
Implements Phases 1–3 of the spec: auth + workspaces, parts/storage/stock with an
append-only ledger, lots, projects with full CSV BOM import.

The existing webcam barcode scanner (`barcodeReader/`) is integrated into `/parts/scan`.

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

## Layout

- `backend/` — FastAPI + SQLAlchemy 2 + Alembic
- `web/` — Vite + React + TypeScript + Tailwind
- `barcodeReader/` — original Scandit assets (source for `web/public/scandit/`)
