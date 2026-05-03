# Runbook: production rollback

Audience: engineer / on-call

Roll back a bad deploy. Two paths: **revert + redeploy** (preferred, keeps
git as source of truth) and **emergency `git reset --hard`** on the VPS
(faster, leaves git out of sync with prod for a minute). Migrations are
the trap door — see "When you cannot rollback" below.

- **When to run**:
  - Post-deploy health gate passed but Sentry / users report a regression
    introduced by the most recent merge.
  - `/api/health` returning 503 after a deploy and the cause is in app
    code (not DB / volume).
  - Sentry release tag matches the bad SHA (see `sentry-triage.md`).
- **Severity**: SEV-1 if prod 5xx > 5%, SEV-2 if a feature is broken but
  the app is up.
- **Time-to-recovery target**: 10 min for revert + redeploy; 5 min for
  emergency VPS reset.
- **Owner**: `<TODO(verify): on-call rotation>`

See `docs/deployment.md` for the deploy pipeline and the `git reset --hard
origin/main` step the CI uses (`.github/workflows/ci.yml:496`). **Don't**
re-explain the deploy — link.

## Pre-flight

- Identify the bad SHA: GitHub Actions → most recent `deploy` job →
  `git rev-parse HEAD` from the SSH log, or `Sentry release` tag on the
  triggering issue.
- Identify the **last-known-good SHA**: the commit immediately before
  the bad one on `main`. `git log --oneline -5 main` gives you both.
- Decide whether the bad commit changed migrations:
  ```bash
  git diff <good-sha>..<bad-sha> -- backend/alembic/versions/
  ```
  If the diff is non-empty, jump to "When you cannot rollback".
- Know which path you're taking. **Default is revert + redeploy**; only
  use the emergency VPS reset if CI is unavailable or you need to be
  back in < 5 min.

## A. Revert + redeploy (preferred)

Re-merges `main` at the good SHA via a normal commit, so the audit trail
is intact and the next deploy doesn't surprise you.

1. From a local checkout on `main`:
   ```bash
   git fetch origin
   git checkout main
   git pull --ff-only
   git revert --no-edit <bad-sha>
   ```
   For a multi-commit revert, use `git revert --no-edit <good-sha>..<bad-sha>`.
2. Push to a branch and open a PR titled
   `revert: <bad-sha> — <one-line reason>`:
   ```bash
   git checkout -b revert-<bad-sha-short>
   git push -u origin revert-<bad-sha-short>
   gh pr create --title "revert: <bad-sha> — <reason>" \
       --body "Reverts <bad-sha>. Triggering issue: <Sentry / GH issue link>."
   ```
3. Get a second pair of eyes; merge to `main`.
4. CI runs the standard pipeline, then the `deploy` job pauses for the
   `production` environment reviewer (CLAUDE.md "Deploy is automatic —
   but gated by a human reviewer"). Approve.
5. Watch the post-deploy health gate in the SSH log
   (`.github/workflows/ci.yml:524-540`) — 30 × 5 s polls of
   `https://parts.matescb.cz/api/health`. Green ping = rollback live.

## B. Emergency VPS reset (only when CI is unavailable)

Bypasses CI. Leaves `main` ahead of prod until you push a follow-up
revert PR. **Use only when** the regression is severe enough that 5 min
of CI lag is unacceptable.

1. SSH in.
   ```bash
   ssh deploy@<vps-host>
   ```
2. Snapshot the DB before touching the deploy:
   ```bash
   /srv/backup/bin/run-backup.sh stockmanager
   ```
3. Pin the rollback target — the last-known-good SHA:
   ```bash
   cd /srv/stockmanager
   git fetch --quiet origin main
   git reset --hard <good-sha>
   ```
4. Re-export the release tag so Sentry groups any new errors against the
   rolled-back code (matches the deploy script at
   `.github/workflows/ci.yml:503-504`):
   ```bash
   export SENTRY_RELEASE
   SENTRY_RELEASE=$(git rev-parse --short=12 HEAD)
   ```
5. Rebuild + restart:
   ```bash
   sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
       up -d --build
   docker image prune -f
   ```
6. Wait for the backend healthcheck (start_period 60 s — see
   `docker-compose.prod.yml:125`):
   ```bash
   for i in $(seq 1 30); do
     curl -fsS https://parts.matescb.cz/api/health && break
     sleep 5
   done
   ```
7. Open the follow-up revert PR (section A, steps 1–3) the moment the
   incident is contained, so `main` reflects what's actually running.

## When you cannot rollback (migrations changed)

Alembic migrations are forward-only in this repo — see CLAUDE.md "Don't
edit a migration file once it's been merged to `main`". If the bad
deploy ran a migration:

- A code-only revert leaves the schema **ahead** of the code. Boot will
  fail with `sqlalchemy.exc.ProgrammingError` on the first query that
  touches the renamed/dropped column.
- The fix is one of:
  1. **Forward-fix**: write a new migration + code change that adapts
     to the changed schema. Faster than restore for additive changes.
  2. **Restore from snapshot**: restore the pre-deploy `pg_dump` (taken
     by `deploy/predeploy-dump.sh` at `.github/workflows/ci.yml:511`)
     and roll the code back. See `migration-recovery.md` for the full
     procedure — that runbook owns this case.

If you are not sure whether the migration is reversible, treat it as
not reversible.

## Verification

- `curl -fsS https://parts.matescb.cz/api/health` → 200 with
  `"status":"ok"`.
- Sentry: no new events with the bad SHA tagged `release` after the
  redeploy timestamp.
- The triggering symptom (the bug that prompted the rollback) is gone
  — verify the specific user-visible behaviour, not just the health
  endpoint.
- `git log -1 --format='%H %s'` on the VPS matches the rolled-back SHA.

## Rollback (of the rollback)

If the rollback itself breaks something:

- Path A: open another revert PR reverting the revert (`git revert
  HEAD` on `main`), merge, deploy.
- Path B: re-run section B with the **bad** SHA as the target. You
  are now in incident-response mode — see `incident-response.md`.

## Post-mortem prompts

- Did CI catch this? (If not: missing test, missing E2E coverage, or
  config-only regression that no test could have caught?)
- Did the post-deploy health gate stay green despite the bug? If yes,
  the gate is too coarse — what signal would have flipped it red?
- Was the bad SHA easy to identify from Sentry's release tag? If not,
  is `SENTRY_RELEASE` being set correctly?
  (`.github/workflows/ci.yml:503-504`).
- Did we take path A or B? If B: was the urgency real, or was CI just
  slow that day?
- If migrations were involved: should this migration have been split
  into "add column nullable" → wait one deploy → "backfill + flip not
  null"?
