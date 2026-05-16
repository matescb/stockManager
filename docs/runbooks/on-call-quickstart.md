# Runbook: on-call quickstart

Audience: engineer / on-call

The first 5 minutes of a page. What to look at, in what order. Specific
to this stack — not a general intro to on-call.

- **When to run**: you got paged.
- **Severity**: n/a (this is the orientation step, not an incident
  category).
- **Time-to-recovery target**: 5 min to triage, then jump to the
  scenario-specific runbook.
- **Owner**: `<TODO(verify): on-call rotation>`

## Pre-flight (do this once, before you go on call)

- SSH access works:
  ```bash
  ssh deploy@<vps-host> "echo ok"
  ```
- Local checkout of the repo with `main` up to date.
- Sentry account: backend project + frontend project (see
  `sentry-triage.md`).
- GitHub Actions: you can read the workflow runs and approve a
  `production` environment deploy.
- This shelf bookmarked: `docs/runbooks/`.
- The escalation contact list (below) is in your phone.

## The first 5 minutes

### 0:00 — Acknowledge

Acknowledge the page so it stops escalating. Note the alert source
(UptimeRobot, Sentry, Healthchecks, manual report).

### 0:30 — Is the site up?

```bash
curl -fsS -o /dev/null -w "%{http_code}\n" https://parts.matescb.cz/api/health
```

| Response | Meaning | Jump to |
|---|---|---|
| 200 | App alive — alert is about a feature, not the host | Sentry / specific feature |
| 503 | Health endpoint reports degraded (DB unreachable, uploads broken) | Steps below + relevant runbook |
| 502 / 504 | Apache up, backend container down or hung | `prod-rollback.md` or `migration-recovery.md` |
| 5xx with HTML | Apache returning its own error page; container probably gone | Same as 502 |
| Connection refused / timeout | Host or Apache down | VPS check below |
| TLS error | Cert renewal failed | `docs/deployment.md#tls-certificate-renewal` |

### 1:00 — What changed in the last hour?

```bash
# From any local checkout
git log --since="2 hours ago" --oneline origin/main
```

If there's a deploy in the last hour, you have a strong suspect.
GitHub Actions → most recent successful `deploy` job has the SHA.

### 2:00 — Check Sentry

Open the backend Sentry project. Sort by **Last Seen**, time window
**Last 1 hour**.

- New issues since the latest deploy? → `sentry-triage.md`
- Same issue, regression after months? → still `sentry-triage.md`
- Nothing new? The error isn't reaching the app — likely a network or
  Apache layer problem, not a backend bug.

### 3:00 — SSH and look at containers

```bash
ssh deploy@<vps-host>
cd /srv/stockmanager
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod ps
```

Expect: `db` healthy, `backend` healthy, `web` running, `backend-init`
exited with code 0. Cron sidecars should also be running:

- `backend-cron` runs `sourcing-cache-sweep` every 3600 seconds.
- `backend-cron-alerts` runs `sourcing-alerts-evaluate` every 900 seconds.
- `backend-cron-sessions` runs `session-purge` and `password-reset-purge`
  every 3600 seconds by default.

Each cron sidecar wraps each job run in `timeout 600`. Log line `timed out
after 600s (exit=124)` means the job hit the 10-minute cap and was
terminated; other non-zero exits are logged as `failed (exit=<code>)`. The
sidecar scheduling loops stay alive after either case and sleep until the next
tick.

If `backend` is `Restarting`:
```bash
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
    logs --tail=80 backend
```
- Alembic error → `migration-recovery.md`
- App import error → likely a deploy regression, see `prod-rollback.md`
- DB connection refused → check `db` container

If `db` is `unhealthy`:
```bash
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
    logs --tail=80 db
```

### 4:00 — Pick a runbook

| Symptom | Runbook |
|---|---|
| Recent deploy is the suspect, app is broken | [`prod-rollback.md`](prod-rollback.md) |
| Migration failure on boot | [`migration-recovery.md`](migration-recovery.md) |
| Sentry issue spike, app still up | [`sentry-triage.md`](sentry-triage.md) |
| Backups missing, restore needed | [`backup-restore.md`](backup-restore.md) |
| Email not sending (signup / invitation) | [`smtp-outage.md`](smtp-outage.md) |
| User reports a sourcing alert did not fire | Alert checks below, then [`smtp-outage.md`](smtp-outage.md) if email dispatch failed |
| DigiKey or Mouser lookup failing | [`provider-outage.md`](provider-outage.md) |
| Workspace is unreachable / disabled / suspected isolation leak | [`workspace-recovery.md`](workspace-recovery.md) |
| Something I can't categorise | [`incident-response.md`](incident-response.md) — declare an incident |

### User Reports a Sourcing Alert Did Not Fire

Check the row state first:

```bash
sudo -u deploy docker compose -f docker-compose.prod.yml --env-file .env.prod \
  exec db psql "$DATABASE_URL" -c \
  "SELECT id, alert_type, enabled, archived_at, last_checked_at, last_notified_at, last_evaluation_state FROM sourcing_alerts WHERE workspace_id = '<WORKSPACE_ID>' ORDER BY created_at DESC LIMIT 20;"
```

- `last_checked_at` is null or stale: the alert evaluator job did not run; check
  `backend-cron-alerts`, which runs `sourcing-alerts-evaluate` every 15 minutes.
- `last_evaluation_state` is null: first evaluation records state and does not notify.
- `last_notified_at` is recent: cooldown may be suppressing another email; compare with
  `cooldown_seconds`.
- Sourcing alert types use cached TrustedParts data by default; a manual fresh refresh
  may show newer provider state than the evaluator saw.
- If the row says it notified but the user has no email, check backend logs for
  `sourcing_alert.smtp_failed` and continue with [`smtp-outage.md`](smtp-outage.md).

Source: `backend/app/domain/sourcing/alerts_evaluator.py:42-88`

## Where the dashboards live

Same URLs as `docs/runbooks/README.md` — repeat here so you don't
context-switch:

- **Sentry (backend)**: `<TODO(verify): URL from SENTRY_PROJECT>`
- **Sentry (frontend)**: `<TODO(verify): URL from VITE_SENTRY_DSN>`
- **UptimeRobot**: `<TODO(verify): monitor URL>`
- **Healthchecks.io** (backup heartbeat):
  `<TODO(verify): from BACKUP_HEALTHCHECK_OK_URL>`
- **GitHub Actions**: `https://github.com/matescb/stockManager/actions`
- **VPS**: `ssh deploy@<vps-host>` — host in `docs/deployment.md`

## Escalation

- **App / backend questions**: `<TODO(verify): primary engineer handle>`
- **Infra / VPS / Apache / certs**: `<TODO(verify): infra owner>`
- **Database / migrations**: `<TODO(verify): db owner — typically same as backend>`
- **DNS / domain (parts.matescb.cz)**: `<TODO(verify): domain owner>`
- **vps-backup service**: see [matescb/vps-backup](https://github.com/matescb/vps-backup)
  — owner in that repo's README.

When to escalate:

- After 15 min if you can't identify the cause.
- Immediately if data loss is in progress.
- Immediately if you're about to run a destructive command and want
  a second pair of eyes.
- Immediately if the issue affects the entire VPS (other projects on
  the same host are also impacted).

## What to communicate (and when)

- T+0: acknowledge the page; note source + your initial read in the
  incident channel.
- T+15: status update — what you've ruled out, what you're trying.
- T+30: if not resolved, declare an incident — see
  `incident-response.md` for cadence and severity.

`<TODO(verify): incident channel name — Slack / Discord / email>`

## What not to do in the first 5 minutes

- Don't `git reset --hard` on the VPS unless the cause is clearly the
  most recent deploy and you've already confirmed migrations didn't
  change. Use the revert+redeploy path (`prod-rollback.md` section A)
  by default.
- Don't restart `db` to "see if it helps". A clean restart of an
  unhealthy DB can mask an underlying disk-full / corruption issue;
  capture logs first.
- Don't `docker compose down -v` — the `-v` flag wipes named volumes,
  including `db_data` and `uploads`. There is no undo.
- Don't rotate secrets reflexively. If you suspect a leak, follow
  `secret-rotation.md`. If you don't suspect a leak, leave them alone.
- Don't post the contents of `.env.prod` anywhere. It contains
  `WORKSPACE_SECRETS_KEY`, which is unrecoverable if leaked.

## Verification

After the immediate fire is out:

- `/api/health` returns 200 with `"status":"ok"`.
- A representative API call (logged-in user fetching their workspace)
  succeeds.
- Sentry stops reporting new events for the triggering issue.
- Whoever paged confirms the symptom is gone.

## Post-mortem prompts

- Did the page have enough information to start triage, or did you
  spend the first 5 min figuring out what was being alerted on?
- Did this runbook take you to the right scenario-specific runbook
  quickly?
- Was a piece of context you needed missing from this page? Add it.
