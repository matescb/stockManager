# Parts Inventory & Production Manager

Self-hosted web app for electronics parts, storage, stock, lots, BOMs and projects.

Implemented phases:

- **1–3** auth + workspaces; parts/storage with the append-only stock ledger; lots; projects with full CSV BOM import.
- **4** purchase orders + line-level receive (creates `source_type='purchase'` lots, ledger rows tagged `order_id`/`order_entry_id`). See `docs/phases/04-orders.md`.
- **5** builds + consume-from-BOM with substitute fallback, optional sub-assembly output lot. See `docs/phases/05-builds.md`.
- **6** read-only reports: low-stock, stock-value (by currency), BOM shortage, expiring lots. See `docs/phases/06-reports.md`.
- **7** BOM import presets — save / recall column mappings in the import wizard. See `docs/phases/07-bom-presets.md`.

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

For running tests outside Docker, see [`docs/development.md`](docs/development.md).

## Layout

- `backend/` — FastAPI + SQLAlchemy 2 + Alembic
- `web/` — Vite + React + TypeScript + Tailwind
- `barcodeReader/` — original Scandit assets (source for `web/public/scandit/`)
