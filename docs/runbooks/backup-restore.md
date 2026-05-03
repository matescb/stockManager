# Runbook: backup verify / manual run / restore

Audience: engineer / on-call

Consumer-side runbook for the **vps-backup** service. Covers verifying
the daily chain, triggering an out-of-cycle backup, and restoring DB or
uploads from a daily/weekly/monthly snapshot. The producer-side service
lives at [matescb/vps-backup](https://github.com/matescb/vps-backup); this
runbook only covers what stockManager operators need to do.

- **When to run**:
  - Routine: weekly verification (Mondays).
  - SEV-1: backup chain broken (Healthchecks.io alert, missing artifact,
    decrypt failure).
  - Pre-flight for any destructive op (migration that drops/rewrites
    columns, `WORKSPACE_SECRETS_KEY` rotation).
- **Severity**: Routine for verify; SEV-1 for restore-after-loss.
- **Time-to-recovery target**: verify < 15 min; restore < 60 min for DB,
  < 30 min for uploads.
- **Owner**: `<TODO(verify): on-call rotation>`

See `docs/deployment.md#backups` (lines 619–777) for architecture, the
GFS retention policy, and the age key escrow procedure. **Don't** restate
that here — link.

## Pre-flight

- SSH access to the VPS as the `deploy` user.
- For restore: the off-VPS **age private key** (`backup-key.txt`) on a
  secure machine. Without it, encrypted artifacts are unreadable.
- For DB restore: confirm the most recent backup actually predates the
  corruption you're recovering from.
- Notify the team — restore takes the stack offline.

## A. Verify the backup chain (routine)

1. SSH in.
   ```bash
   ssh deploy@<vps-host>
   ```
2. Inspect the local 7-day window:
   ```bash
   ls -lh /srv/backups/stockmanager/ | head -20
   ```
   Expect one `db-YYYY-MM-DD.sql.gz.age` and one
   `assets-YYYY-MM-DD.tar.gz.age` per day for the last 7 days.
3. Inspect the NAS chain:
   ```bash
   ls -lh /mnt/nas-backups/stockmanager/daily/   | tail -20
   ls -lh /mnt/nas-backups/stockmanager/weekly/  | tail -20
   ls -lh /mnt/nas-backups/stockmanager/monthly/ | tail -20
   ```
   Expect 14 daily, 8 weekly (Sundays), 6 monthly (1st of month).
4. Check the size floor — a truncated dump is silent failure:
   ```bash
   stat -c '%n %s' /srv/backups/stockmanager/db-*.sql.gz.age | sort
   ```
   The DB artifact should be > 100 KB even on a fresh DB. Sudden drop to
   a few hundred bytes means `pg_dump` failed mid-stream.
5. Tail the runner log:
   ```bash
   tail -200 /var/log/vps-backup.log
   ```
   Look for `OK` lines per artifact and the closing healthcheck ping.
6. Confirm Healthchecks.io shows green. URL:
   `<TODO(verify): from BACKUP_HEALTHCHECK_OK_URL in /srv/stockmanager/.env.prod>`.

## B. Trigger a manual backup (pre-flight before risky op)

1. SSH in.
2. Run the project-agnostic runner with the stockmanager profile:
   ```bash
   /srv/backup/bin/run-backup.sh stockmanager
   ```
   The runner does pg_dump, age-encrypt, NAS push, GFS-prune, and pings
   `BACKUP_HEALTHCHECK_OK_URL` on success.
3. Verify the new artifacts landed:
   ```bash
   ls -lh /srv/backups/stockmanager/ | tail -2
   ls -lh /mnt/nas-backups/stockmanager/daily/ | tail -2
   ```
4. Confirm size sanity (compare against yesterday's dump).

Non-zero exit pings `BACKUP_HEALTHCHECK_FAIL_URL` and surfaces in
`/var/log/vps-backup.log` — see `docs/deployment.md:671-672`.

## C. Restore the DB from a snapshot (SEV-1)

1. Bring the off-VPS age private key onto the VPS for the restore window:
   ```bash
   scp backup-key.txt deploy@<vps-host>:/tmp/
   ```
2. SSH in.
3. **Dry-run first** — checks decrypt + gunzip integrity, then aborts:
   ```bash
   /srv/backup/bin/restore.sh stockmanager 2026-05-02 db
   # prompts for the path to the identity file → /tmp/backup-key.txt
   ```
   The script walks `daily/` → `weekly/` → `monthly/` automatically; the
   date is the artifact date, not the cadence tier.
4. Stop the backend so no writes land mid-restore:
   ```bash
   cd /srv/stockmanager
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       stop backend
   ```
5. Run the destructive restore:
   ```bash
   /srv/backup/bin/restore.sh stockmanager 2026-05-02 db --confirm-destructive
   ```
6. Restart the backend (it will run `alembic upgrade head` against the
   restored schema on boot — see `docker-compose.prod.yml:148`):
   ```bash
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       up -d backend
   ```
7. Scrub the key from the VPS:
   ```bash
   shred -u /tmp/backup-key.txt
   ```

## D. Restore the uploads volume

1. Same key-scp + dry-run pattern as section C.
2. Real restore — same shape, `assets` subject:
   ```bash
   /srv/backup/bin/restore.sh stockmanager 2026-05-02 assets --confirm-destructive
   ```
3. Verify a known asset URL still resolves through the API
   (`GET /api/parts/assets/<ws_id>/<filename>` — see CLAUDE.md
   "Content-addressed assets").
4. Scrub the key.

## Verification

- `curl -fsS https://parts.matescb.cz/api/health` returns 200 with
  `"status":"ok"`.
- A representative `GET /api/parts` call returns rows (DB intact).
- A datasheet asset URL serves a non-empty file (uploads intact).
- For section A: Healthchecks.io check is green and `tail` of the runner
  log shows today's success ping.

## Rollback

- A failed restore can be retried with the **previous** date — the
  current snapshot is untouched, the script writes into `db_data` /
  `uploads` directly. If both daily and weekly restores fail, fall back
  to the latest `monthly/` artifact.
- If the DB volume is half-restored and the schema is inconsistent,
  shut the backend down and re-run section C with an older date — do
  **not** try to `alembic stamp` over a partial restore.
- If the age private key has been lost: encrypted artifacts are
  permanently unrecoverable. See `docs/runbooks/secret-rotation.md`
  section 2.6 for the analogous `WORKSPACE_SECRETS_KEY` lesson — escrow
  is mandatory.

## Post-mortem prompts

- Did the failure surface via Healthchecks.io within the expected
  window, or did we discover it some other way?
- Was the most recent intact snapshot the daily, weekly, or monthly?
  (Tells us how many days of data were lost.)
- Was the size-floor check enough to flag the corruption, or do we need
  a content check (e.g. `pg_restore --list` on the artifact)?
- Did the restore script's dry-run catch any issue before
  `--confirm-destructive`?
- Did the post-restore `alembic upgrade head` succeed, or did we recover
  to a schema older than `main`? (See `migration-recovery.md`.)
