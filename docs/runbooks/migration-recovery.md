# Runbook: failed alembic migration

Audience: engineer / on-call

Recover from a migration that left prod in a broken state. Migrations
run on backend container start (`docker-compose.prod.yml:161` —
`alembic upgrade head && exec uvicorn …`). A failure aborts the boot,
the healthcheck never goes green, and the post-deploy gate at
`.github/workflows/ci.yml:669-680` fails the deploy job. The previous
container keeps serving until you intervene — but the new container is
restarting on a loop and may have applied the migration partially.

- **When to run**:
  - GitHub Actions `deploy` job failed at the post-deploy health gate.
  - Backend container in `Restarting` state after a deploy.
  - Backend logs show `alembic.util.exc.CommandError` or
    `sqlalchemy.exc.ProgrammingError` during `alembic upgrade head`.
- **Severity**: SEV-1.
- **Time-to-recovery target**: 30–60 min (forward-fix) or 60–90 min
  (point-in-time restore).
- **Owner**: `<TODO(verify): on-call rotation>`

See `docs/deployment.md#backups` and `docs/runbooks/backup-restore.md`
for restore mechanics, and `docs/runbooks/prod-rollback.md` for code
rollback. This runbook owns the **schema** side.

## Pre-flight

- SSH access to the VPS as `deploy`.
- Git checkout of the repo locally so you can write a forward-fix
  migration.
- The pre-deploy `pg_dump` from this deploy. Path:
  `<TODO(verify): output dir of deploy/predeploy-dump.sh, expected under
  /srv/backups/stockmanager/predeploy/<sha>.sql.gz>`. Confirm the file
  exists **before** you decide on the recovery path.
- The `WORKSPACE_SECRETS_KEY` value (you'll need to confirm it still
  decrypts a sample row after restore — see step 7 in path B).

## Step 0 — Stop the restart loop and put up the maintenance page

A backend that crashes mid-`alembic upgrade` and restarts every few
seconds will leave Postgres holding migration locks and fill logs with
noise. Stop it.

1. SSH in.
2. Stop the backend (leave `db` and `web` up):
   ```bash
   ssh deploy@<vps-host>
   cd /srv/stockmanager
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       stop backend
   ```
3. Switch Apache to the maintenance vhost so users see the static page,
   not 502s. The maintenance page lives at `deploy/maintenance.html`
   and the alternate vhost at `deploy/parts.matescb.cz.maintenance.conf`.
   ```bash
   sudo a2dissite parts.matescb.cz parts.matescb.cz-le-ssl
   sudo cp /srv/stockmanager/deploy/parts.matescb.cz.maintenance.conf \
       /etc/apache2/sites-available/
   sudo a2ensite parts.matescb.cz.maintenance
   sudo systemctl reload apache2
   ```
   `<TODO(verify): exact vhost filename / a2ensite mechanics on this VPS>`

## Step 1 — Examine the schema state

Migrations are not transactional across the whole upgrade — each
migration runs in its own transaction, but the failure may have
committed N-1 migrations and rolled back the Nth. Find out where you
are.

1. Open psql:
   ```bash
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       exec db psql -U stockmgr stockmgr
   ```
2. Check the alembic version table:
   ```sql
   SELECT version_num FROM alembic_version;
   ```
3. Compare against the latest revision in
   `backend/alembic/versions/` — `ls backend/alembic/versions/ | sort -V
   | tail -3` gives the last three.
4. If the version_num matches the **bad** migration's revision_id, the
   migration committed and the failure was downstream (e.g. a data
   backfill at app boot). If it matches the **previous** revision, the
   migration rolled back.
5. If you see a `psql: ERROR: ... advisory lock` warning, alembic still
   holds the migration lock. Find and terminate it:
   ```sql
   SELECT pid, state, query FROM pg_stat_activity
    WHERE datname = 'stockmgr' AND state != 'idle';
   SELECT pg_terminate_backend(<pid>);
   ```

## Step 2 — Decide: forward-fix vs restore

| Symptom | Path |
|---|---|
| Migration ran, schema is ahead of code, code can be patched in < 30 min | A. Forward-fix |
| Migration corrupted data (e.g. backfill loop wrote bad rows) | B. Restore |
| Migration is structurally wrong (column missing, wrong type) and not safely reversible | B. Restore |
| Migration partially applied + advisory lock stuck | B. Restore |

When in doubt, restore. The pre-deploy `pg_dump` exists for exactly
this case (CLAUDE.md "There is no staging environment").

## Downgrading migration 0058 after `lots.part_id` was set NULL

Migration 0058 changes `lots.part_id` from `ON DELETE CASCADE` / `NOT
NULL` to `ON DELETE SET NULL` / nullable. If any parts were hard-deleted
after 0058 was applied, Postgres may have preserved their lots by
setting `lots.part_id = NULL`. The downgrade intentionally refuses to
restore the old constraint while those rows exist:

```sql
SELECT workspace_id, count(*) AS orphan_lots
  FROM lots
 WHERE part_id IS NULL
 GROUP BY workspace_id
 ORDER BY workspace_id;
```

Before running `alembic downgrade 0057`, choose one remediation path:

1. Re-archive the deleted parent parts from backup/audit data, then
   reconnect the orphaned lots to those archived part rows.
2. Hard-delete orphaned lots that the incident commander confirms can be
   discarded.

Use SQL like this in a transaction, replacing the `VALUES` list with the
operator-reviewed lot-to-restored-part mapping:

```sql
BEGIN;

-- Inspect the affected rows first.
SELECT id, workspace_id, name, serial_number, created_at, archived_at
  FROM lots
 WHERE part_id IS NULL
 ORDER BY workspace_id, created_at;

-- Path 1: reconnect preserved lots to parts that were restored as archived
-- rows. The restored parts must already exist in `parts`.
WITH restored_parent(lot_id, part_id) AS (
  VALUES
    ('<lot-id>'::uuid, '<restored-archived-part-id>'::uuid)
)
UPDATE lots AS l
   SET part_id = restored_parent.part_id,
       archived_at = COALESCE(l.archived_at, now()),
       updated_at = now()
  FROM restored_parent
 WHERE l.id = restored_parent.lot_id
   AND l.part_id IS NULL
   AND EXISTS (
       SELECT 1
         FROM parts AS p
        WHERE p.id = restored_parent.part_id
          AND p.workspace_id = l.workspace_id
          AND p.archived_at IS NOT NULL
   );

-- Path 2: if approved, discard any remaining orphaned lots before
-- downgrading. Deleting lots will SET NULL on dependent stock entries.
DELETE FROM lots
 WHERE part_id IS NULL;

-- Must return zero rows before the downgrade can succeed.
SELECT id, workspace_id
  FROM lots
 WHERE part_id IS NULL;

COMMIT;
```

Do not edit 0058 to make it silently reversible; the NULL rows represent
history that the old schema cannot express without operator input.

## Downgrading through migration 0062 after manually recreating the password reset index

Migration 0062 drops `ix_password_reset_requests_expires_at`; its
downgrade recreates the same index
(`backend/alembic/versions/0062_password_reset_purge_index.py:24-33`).
If `ix_password_reset_requests_expires_at` was manually recreated during
recovery before downgrading through 0062, the downgrade will hit a
duplicate-index error. Drop the manual copy, or avoid the duplicate index
before running the downgrade.

Check before any `alembic downgrade` that crosses from 0062 to 0061:

```sql
SELECT to_regclass('public.ix_password_reset_requests_expires_at') AS index_name;
```

If the query returns `public.ix_password_reset_requests_expires_at`, drop
the index in the maintenance window and let the 0062 downgrade recreate it:

```sql
DROP INDEX IF EXISTS ix_password_reset_requests_expires_at;
```

Do not edit 0062 to add `IF NOT EXISTS`; merged migrations are immutable.

## Path A — Forward-fix

1. From a local checkout on `main`:
   ```bash
   git checkout -b hotfix-migration-<sha-short>
   ```
2. Write a new migration that brings the schema to a working state.
   Use `down_revision = '<bad-rev-id>'` so it stacks on top of what's
   in prod. **Do not edit the failed migration file** — CLAUDE.md
   "Don't edit a migration file once it's been merged to `main`".
3. Patch any application code that depends on the now-changed schema.
4. Run locally:
   ```bash
   docker compose -f docker-compose.dev.yml exec backend pytest -k <relevant>
   ```
5. Confirm the alembic single-head invariant:
   ```bash
   docker compose -f docker-compose.dev.yml exec backend python -m alembic heads
   ```
   Must print exactly one head (matches the CI guard at
   `.github/workflows/ci.yml:232-237`).
6. PR, review, merge. CI will deploy. Restart the backend manually if
   the deploy job times out:
   ```bash
   ssh deploy@<vps-host>
   cd /srv/stockmanager && git fetch origin main && git reset --hard origin/main
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       up -d --build backend
   ```
7. Skip to "Verification".

## Path B — Point-in-time restore

Restores the DB to the pre-deploy snapshot, rolls the code back to the
matching SHA, brings the stack up.

1. Identify the pre-deploy snapshot:
   ```bash
   ls -lh /srv/backups/stockmanager/predeploy/ | tail -5
   ```
   The snapshot for the failed deploy is named after the SHA you tried
   to deploy (`<TODO(verify): exact naming from deploy/predeploy-dump.sh>`).
2. Identify the matching code SHA — the parent of the failed deploy:
   ```bash
   cd /srv/stockmanager
   git log --oneline -5
   # last-known-good = the commit before the bad SHA
   ```
3. Stop backend (already done in step 0). Stop web too:
   ```bash
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       stop web
   ```
4. Run a fresh on-demand backup as a "last good before restore" anchor:
   ```bash
   /srv/backup/bin/run-backup.sh stockmanager
   ```
5. Restore the DB from the pre-deploy snapshot — see
   `docs/runbooks/backup-restore.md` section C. Use the predeploy
   snapshot path, not the daily one.
6. Reset the working tree to the last-known-good SHA:
   ```bash
   git reset --hard <good-sha>
   ```
7. Confirm `WORKSPACE_SECRETS_KEY` still decrypts a sample workspace
   credential (the key didn't change during the bad deploy, but the
   restored row count did — sanity-check that `_fernet().decrypt(...)`
   round-trips on a row from `workspaces`):
   ```bash
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       exec backend python -c "
   from app.core.secrets import _fernet
   from sqlalchemy import create_engine, text
   from app.core.config import settings
   eng = create_engine(settings().DATABASE_URL)
   with eng.begin() as c:
       row = c.execute(text(\"SELECT parts_provider_api_key FROM workspaces WHERE parts_provider_api_key IS NOT NULL LIMIT 1\")).scalar()
   if row: print('decrypt:', _fernet().decrypt(row.encode())[:4], '...')
   else:   print('no encrypted creds present — nothing to verify')
   "
   ```
   The encrypted credential columns on `workspaces` are `parts_provider_api_key` and `parts_provider_api_secret` (`backend/app/domain/workspaces/models.py:45,47`). The `workspace_catalog_tokens` child table holds catalog-token HMACs separately.
8. Bring the stack back:
   ```bash
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       up -d --build
   ```
9. Wait for the healthcheck (start_period 60 s — `docker-compose.prod.yml:125`).

## Step 3 — Restore the public vhost

Once `/api/health` is green:

```bash
sudo a2dissite parts.matescb.cz.maintenance
sudo a2ensite parts.matescb.cz parts.matescb.cz-le-ssl
sudo systemctl reload apache2
```

## Verification

- `curl -fsS https://parts.matescb.cz/api/health` → 200 with
  `"status":"ok"`.
- `SELECT version_num FROM alembic_version;` matches the head expected
  for the running code SHA.
- A representative read (`GET /api/parts`) returns rows.
- A representative write (create part / append stock entry) succeeds.
- Sentry: no new `ProgrammingError` events tagged with the current
  release.

## Rollback

- Path A failed: write another migration that fixes the fix, or fall
  back to path B.
- Path B failed: try the previous daily snapshot (you'll lose less
  than 24 h of data, but the schema will at least be consistent).
- If the predeploy snapshot is missing or corrupt: this is a
  backup-chain incident — see `backup-restore.md` and treat the data
  loss accordingly.

## Post-mortem prompts

- Was the migration tested against a prod-shape dataset locally?
- Did `alembic upgrade head` succeed in CI? CI runs against an empty
  test DB — that catches schema sanity but not data-shape issues.
- Should this migration have been split (additive deploy → backfill →
  constraint flip)?
- Did `predeploy-dump.sh` actually fire and succeed? Review its log.
- How long was prod on the maintenance page? Is there a way to keep
  reads serving from the **previous** container while the migration
  runs?
