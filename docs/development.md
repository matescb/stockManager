# Local development

## Stack

- Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Postgres 16
- Node 18+, Vite 5, React 18, TypeScript 5, Tailwind 3, react-query 5

## Running with Docker (recommended)

```bash
cp .env.example .env
docker compose up --build
```

- Web UI: http://localhost:5173
- API:    http://localhost:8000/api

The backend container runs `alembic upgrade head` before booting `uvicorn`.

## Running tests outside Docker

The backend test suite needs Postgres. With a local Postgres:

```bash
sudo apt-get install -y postgresql
sudo systemctl start postgresql
sudo -u postgres psql <<'SQL'
CREATE USER stockmgr WITH PASSWORD 'stockmgr' CREATEDB;
CREATE DATABASE stockmgr_test OWNER stockmgr;
SQL

python3 -m venv .venv
.venv/bin/pip install -e backend -e backend[dev]

TEST_DATABASE_URL="postgresql+psycopg://stockmgr:stockmgr@127.0.0.1:5432/stockmgr_test" \
    .venv/bin/python -m pytest -q
```

The `tests/conftest.py` fixture nukes & recreates the public schema between
tests, so re-running the suite does not require manual cleanup.

## Migrations

The schema is managed by Alembic. Each phase adds one revision file under
`backend/alembic/versions/`:

| File | What |
|------|------|
| `0001_initial.py` | Phase 1–3 schema (auth, parts, storage, stock ledger, lots, projects, BOMs) |
| `0002_orders.py`  | Phase 4: orders + order_entries |

The `parts ↔ projects` circular FK is broken with `use_alter=True` on
`Project.associated_subassembly_part_id`; `0001` emits an explicit
`op.create_foreign_key(...)` after both tables exist.

To autogenerate a new revision after a model change:

```bash
DATABASE_URL=… .venv/bin/alembic revision --autogenerate -m "description"
```

Review the generated file (autogenerate is not perfect — especially around
`use_alter` FKs and constraint names) and rename to a stable
`NNNN_short_name.py` filename.
