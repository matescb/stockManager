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

### Slow tests

The migration round-trip suite (`tests/test_migrations.py`,
TEST-007) is marked `@pytest.mark.slow` and excluded by the default
`pytest` invocation. To run it:

```bash
TEST_DATABASE_URL=…  python -m pytest -m slow -q
```

The round-trip uses a sibling DB (`<your_test_db>_migration_rt`)
created on first run so concurrent test runs don't trample each
other. Override the URL via `MIGRATION_TEST_DATABASE_URL` if needed.

## Migrations

The schema is managed by Alembic. Migrations are linear (one chain,
`0001 → 0002 → … → 0012`) and live under `backend/alembic/versions/`:

| File | What |
|------|------|
| `0001_initial.py` | Phase 1–3 schema (auth, parts, storage, stock ledger, lots, projects, BOMs) |
| `0002_orders.py`  | Phase 4: orders + order_entries |
| `0003_builds.py`  | Phase 5: builds + reservations |
| `0004_part_serialized.py` | Phase 9 prep: `Part.serialized` flag |
| `0005_workspace_invitations.py` | Phase 10: invitations table |
| `0006_workspace_catalog_token.py` | Public token-gated `/catalog/{token}` route |
| `0007_workspace_parts_provider.py` | Per-workspace MPN-lookup provider config |
| `0008_spec_source_part_link_metadata.py` | `CustomField.source` + `Part.linked_*` columns |
| `0009_workspace_parts_provider_secret.py` | DigiKey OAuth secret column |
| `0010_workspace_scanner.py` | Scanner backend choice (Scandit vs ZXing) |
| `0011_parts_mpn_unique.py` | Partial unique index `uq_parts_ws_mpn` |
| `0012_stock_entries_bag_signature.py` | Bag re-scan recognition |

Migrations stopped corresponding 1:1 with phase docs after `0005` —
the per-phase doc model retired with Phase 10. Post-`0005` migrations
are described in `CHANGELOG.md` instead.

### Cyclic foreign keys

The `parts ↔ projects` circular FK is broken with `use_alter=True` on
`Project.associated_subassembly_part_id`. Today this is the only
cyclic FK in the schema, but the convention is enforced only by
precedent — any future cycle MUST follow the same three steps or
`alembic downgrade` will fail mid-way. DB-012 / issue #103.

1. **Inside the `create_table` call**, declare the FK with
   `ForeignKeyConstraint(..., use_alter=True, name="fk_<table>_<col>")`
   so SQLAlchemy emits a deferred constraint instead of trying to
   resolve the cycle inline. See `0001_initial.py:128`.
2. **After both `create_table` calls** in the same `upgrade()` body,
   emit an explicit `op.create_foreign_key(name=..., ...,
   use_alter=True)`. See `0001_initial.py:382-388`.
3. **At the top of `downgrade()`**, drop the alter-FK *before* any
   `drop_table`. Otherwise the table-drop fails because the FK still
   references it. See `0001_initial.py:394`.

Worked example shape (the canonical names live in `0001_initial.py`):

```python
# inside create_table
sa.ForeignKeyConstraint(
    ["associated_subassembly_part_id"], ["parts.id"],
    use_alter=True,
    name="fk_projects_associated_subassembly_part",
)

# in upgrade(), after both tables exist
op.create_foreign_key(
    "fk_projects_associated_subassembly_part",
    "projects", "parts",
    ["associated_subassembly_part_id"], ["id"],
    use_alter=True,
)

# in downgrade(), FIRST line
op.drop_constraint(
    "fk_projects_associated_subassembly_part",
    "projects", type_="foreignkey",
)
```

Round-trip coverage of this codepath beyond `tests/conftest.py`'s
fresh-schema rebuild is tracked in #109 (TEST-007).

To autogenerate a new revision after a model change:

```bash
DATABASE_URL=… .venv/bin/alembic revision --autogenerate -m "description"
```

Review the generated file (autogenerate is not perfect — especially around
`use_alter` FKs and constraint names) and rename to a stable
`NNNN_short_name.py` filename. **Don't edit a migration in place once
it's on `main`** — it has already been auto-deployed to prod, and
editing breaks the alembic chain on the next `alembic upgrade head`. Add
a new migration instead.
