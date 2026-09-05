# Local development

## Stack

- Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Postgres 16
- Node 18+, Vite 5, React 18, TypeScript 5, Tailwind 3, react-query 5

## Running with Docker (recommended)

```bash
cp .env.example .env   # first time only — .env.example includes SESSION_SECRET
make dev-up
```

- Web UI: http://localhost:5173
- API:    http://localhost:8000/api

The backend container runs `alembic upgrade head` before booting `uvicorn`.

The dev compose file is `docker-compose.dev.yml`; use `make dev-*` targets
to avoid accidentally starting the dev stack with no `SESSION_SECRET` set.
The `Makefile` also exposes `prod-up`, `prod-logs`, and `prod-rebuild`
targets for the prod compose file.

### Generated frontend assets

Two `web/scripts/*.mjs` steps run as `predev` / `prebuild` (and, for the
second, `pretest`) and write into gitignored directories:

| Script | Writes | Why |
|---|---|---|
| `copy-zxing-wasm.mjs` | `web/public/zxing/` | serve the scanner wasm from our own origin, not a CDN |
| `copy-docs.mjs` | `web/src/generated/` | inline the `docs/user/` manual + `CHANGELOG.md` into the bundle for `/help` and `/about` |

`copy-docs.mjs` resolves its sources relative to the repo root, so it
needs `docs/user/` and `CHANGELOG.md` to sit two levels above
`web/scripts/`. That holds in a checkout; the dev container gets them as
read-only bind mounts (`docker-compose.dev.yml`) and the prod image gets
them via explicit `COPY` lines in `web/Dockerfile.prod` (plus a
`!CHANGELOG.md` negation in `.dockerignore`, which otherwise drops every
root-level `*.md`). Running `vitest` directly rather than through
`npm test` skips `pretest` — run `node scripts/copy-docs.mjs` once first
if `src/generated/` is missing.

## Adding or updating Python dependencies

The backend uses [uv](https://docs.astral.sh/uv/) for deterministic
dependency management.  Two lockfiles live in `backend/`:

| File | Purpose |
|------|---------|
| `uv.lock` | Full resolution graph (uv native format). Committed to track the exact package graph and support `uv lock --check` in CI. |
| `requirements.lock` | Flat hashed requirements file. Used by CI's `pip-audit` job to audit the pinned dependency set for CVEs. |

### To add a new runtime dependency

```bash
cd backend
uv add <package>               # updates pyproject.toml + uv.lock
uv export --format requirements-txt --hashes --no-dev --no-emit-project \
    -o requirements.lock       # regenerate the hashed flat file
```

Commit all three files (`pyproject.toml`, `uv.lock`, `requirements.lock`)
together.  The CI `lockfile-drift` job will fail on any PR where
`pyproject.toml` and `uv.lock` are out of sync, or where
`requirements.lock` does not match `uv.lock`.

### To add a dev-only dependency

```bash
cd backend
uv add --dev <package>
# requirements.lock is NOT regenerated — dev deps are excluded from it.
# Only uv.lock and pyproject.toml change.
```

### To upgrade a dependency

```bash
cd backend
uv lock --upgrade-package <package>
uv export --format requirements-txt --hashes --no-dev --no-emit-project \
    -o requirements.lock
```

## Lint gates and baselines

CI enforces lint via `scripts/lint-delta.sh`, which compares current linter
output against checked-in baseline files and fails only on NEW violations.
This means you can introduce a PR without being blocked by pre-existing
warnings — but you must not add new ones.

Baseline files live at the repo root:

| File | Linter |
|------|--------|
| `.ruff-baseline.txt` | `ruff check app` (Python, runs from `backend/`) |
| `.eslint-baseline.txt` | `eslint src --format=unix` (JS/TS, runs from `web/`) |

### Updating lint baselines

After fixing a batch of pre-existing violations, regenerate the baselines
and commit them together with the fixes:

```bash
# Python (ruff)
cd backend
ruff check app --output-format=concise 2>/dev/null \
  | grep -E '^app/' | sort > ../.ruff-baseline.txt

# JS/TS (eslint)
cd web
npm run lint -- --format=unix 2>/dev/null \
  | grep -E '^[^[:space:]].+:[0-9]+:[0-9]+:' \
  | sed "s|^$(pwd)/||g" \
  | sort > ../.eslint-baseline.txt
```

Commit both baseline files alongside the code fix. The CI delta check will
then see the new (smaller) baseline and pass.

## Running tests outside Docker

The backend test suite needs Postgres. With a local Postgres and
[uv installed](https://docs.astral.sh/uv/getting-started/installation/):

```bash
sudo apt-get install -y postgresql
sudo systemctl start postgresql
sudo -u postgres psql <<'SQL'
CREATE USER stockmgr WITH PASSWORD 'stockmgr' CREATEDB;
CREATE DATABASE stockmgr_test OWNER stockmgr;
SQL

cd backend
uv sync --frozen --extra dev

TEST_DATABASE_URL="postgresql+psycopg://stockmgr:stockmgr@127.0.0.1:5432/stockmgr_test" \
    uv run python -m pytest -q
```

### Fixture isolation

`tests/conftest.py` runs Alembic to head **once per pytest session** (in
the session-scope `engine` fixture). Per-test isolation comes from the
SQLAlchemy "Joining a Session into an External Transaction" pattern:
the `db` fixture opens a connection, begins an outer transaction, opens
a SAVEPOINT, and listens for `after_transaction_end` to restart the
savepoint whenever inner code commits. At teardown the outer
transaction is rolled back, so every row written during the test
evaporates — including writes made by route handlers that call
`db.commit()` (those land on a SAVEPOINT, not the outer transaction).

`client` and `authed_client` both depend on `db`, so HTTP tests get
clean state by construction. Direct `SessionLocal()` use inside a test
also lands on the same connection (we monkey-patch `SessionLocal` for
the duration of the test).

The one exception is tests that need real cross-connection commits —
`test_stock_concurrency.py` spawns threads that each open a separate
HTTP client and need to see each other's writes. That file rolls its
own `authed` fixture and goes through the production code paths; if
you write a similar test, request the `real_db` fixture instead of
`db`. `real_db` does a hard schema reset and Alembic upgrade and is
~1000× slower per test — only use it when the savepoint pattern truly
can't model the test.

## Advisory lock class IDs

Postgres two-int advisory locks use `classid` as the feature namespace
and `objid` as the per-feature key. Allocate a unique int4 class ID here
before adding a new hashtext-backed advisory lock.

| Class ID | Feature | Object ID |
|----------|---------|-----------|
| `1` | `run_job` | `hashtext(job_name)` for allow-listed maintenance jobs. |
| `2` | `password_reset_throttle` | `hashtext("reset:" || email_hash)` for password-reset request throttling. |

### Polymorphic cleanup

SQLAlchemy `before_delete` mapper events do not fire for bulk
`delete().where(...)` statements. Hard-delete code for polymorphic parent
models must either delete ORM instances so the listeners in
`backend/app/domain/_polymorphic_cleanup.py:164-168` run, or explicitly call
`purge_polymorphic(...)` (`backend/app/domain/_polymorphic_cleanup.py:100-121`);
use `backend/scripts/purge_polymorphic_orphans.py` as the safety net for any
bulk-delete path that bypasses mapper events.

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

### E2E tests

Playwright tests live under `web/e2e/`; see
[`web/e2e/README.md`](../web/e2e/README.md) for fixture and tag rules.

CI has three E2E tiers:

| Tier | CI job | Contract |
|------|--------|----------|
| `@smoke` | `playwright-e2e` in `.github/workflows/ci.yml` | Deploy-gating full-stack smoke path. |
| `@core` | `playwright-core` in `.github/workflows/ci.yml` | Advisory, label-gated on `area:frontend` / `area:testing`. |
| `@nightly` | `.github/workflows/playwright-nightly.yml` | Scheduled/manual heavy flows with a 30-day report artifact. |

Local smoke loop:

```bash
make dev-up
cd web && npx playwright test --project=smoke
```

E2E seeding goes through public `/api/*` routes with a real session cookie
from `authedPage`. Do not add backend test-mode endpoints or database
shortcuts for Playwright setup; the point is to exercise the same API
envelope, auth, and workspace rules the browser uses.

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

Known non-contiguous Alembic revision IDs are documented in
[`backend/alembic/versions/_GAPS.md`](../backend/alembic/versions/_GAPS.md).

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

### Migration patterns: expanding CHECK constraints

When expanding a CHECK constraint on a populated table, don't drop the old
constraint and add the replacement in one step. That pattern removes the
guard while Postgres scans the table and takes the heavier lock path that
AUD-042 flagged in
`backend/alembic/versions/0047_alerts_add_data_alerts.py:40-50`.

Use a second constraint name, validate it, then do the short name swap:

```python
_OLD_CK = "sourcing_alerts_alert_type_check"
_NEW_CK = "sourcing_alerts_alert_type_check_v2"

op.execute(
    sa.text(
        f"""
        ALTER TABLE sourcing_alerts
        ADD CONSTRAINT {_NEW_CK}
        CHECK ({_alert_type_check(_ORIGINAL_ALERT_TYPES + _NEW_ALERT_TYPES)})
        NOT VALID
        """
    )
)
op.execute(
    sa.text(f"ALTER TABLE sourcing_alerts VALIDATE CONSTRAINT {_NEW_CK}")
)
op.drop_constraint(_OLD_CK, "sourcing_alerts", type_="check")
op.execute(
    sa.text(
        f"ALTER TABLE sourcing_alerts RENAME CONSTRAINT {_NEW_CK} TO {_OLD_CK}"
    )
)
```

Why this order:

- `ADD CONSTRAINT ... NOT VALID` installs the new rule for future writes
  without scanning historical rows during the add.
- `VALIDATE CONSTRAINT` scans existing rows while the old constraint still
  protects writes.
- Dropping the old constraint and renaming the new one are the short final
  switch. Deploy application code that writes the newly allowed values only
  after this migration has run.

This is for future CHECK expansions. Do not retrofit merged migrations only
to change their locking pattern; write a new migration only when the schema
itself must change.

### Migration patterns: widen vs shrink varchar

Postgres treats `varchar(N) → varchar(M)` asymmetrically:

- **Widening (`M > N`)** is a catalog-only change. No table rewrite,
  no `USING` clause, no truncation risk. The pattern landed in
  `0016_encrypt_workspace_secrets.py` is canonical:

  ```python
  op.alter_column(
      "workspaces", "parts_provider_api_key",
      existing_type=sa.String(255),
      type_=sa.String(1024),
      existing_nullable=True,
  )
  ```

- **Shrinking (`M < N`)** hard-fails on the first row whose value is
  longer than `M` and never auto-truncates. Always add
  `postgresql_using="left(col, M)"` and a regression test that
  inserts a too-long row before the migration:

  ```python
  op.alter_column(
      "workspaces", "scanner_license_key",
      existing_type=sa.String(4096),
      type_=sa.String(2048),
      existing_nullable=True,
      postgresql_using="left(scanner_license_key, 2048)",
  )
  ```

  Truncating user data is destructive — verify the shrink target is
  larger than every existing value in the workspace before merging.
  DB-011 / issue #102.

### Migrations must be self-contained (DB-010)

A migration is a snapshot of schema-at-revision; its behaviour should
not depend on whichever app revision happens to be checked out at
upgrade time. Concretely: **don't `from app.<...>` from inside a
migration's `upgrade()` / `downgrade()`.** If the imported helper is
later renamed or refactored, replaying the migration on a fresh DB
(CI clean checkout, dev reset, disaster recovery) breaks.

If a migration genuinely needs shared logic (e.g. encrypt-at-rest
backfill), copy the helper into a frozen shim under
`app/core/_<name>_v<NNNN>.py` and import from there. The
`_v<NNNN>` suffix encodes which migration the shim is bound to, and
the file is treated as immutable from the moment it lands.

Conventions:

- Frozen shim names match `_<name>_v<NNNN>` (matches
  `tests/test_migration_isolation.py`'s allow-list regex).
- A signature-pinning test (e.g. `tests/test_secrets_signature_pinning.py`)
  asserts the live module's public API still equals the shim's, so a
  later rename surfaces as a CI failure.
- One known exception: migration `0016_encrypt_workspace_secrets.py`
  pre-dates this convention, is already on prod, and is allow-listed.
  Its safety net is the signature-pinning test.
