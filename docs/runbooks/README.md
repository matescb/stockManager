# Runbooks

Audience: engineer / on-call

How to handle ops scenarios. Each runbook follows the template in [STYLE.md](../STYLE.md#runbook-pages-docsrunbooksscenariomd): When · Severity · TTR · Pre-flight · Steps · Verify · Rollback · Post-mortem.

## Severity matrix

| Severity | Definition | Example | Response time |
|---|---|---|---|
| **SEV-1** | Production down or data loss in progress | DB unreachable, prod 5xx rate > 5%, backup chain broken | < 15 min |
| **SEV-2** | Partial outage; users can work around | One feature broken (e.g. provider lookup), slow responses | < 1 hour |
| **SEV-3** | Degradation, not outage | Sentry noise spike, single workspace issue | < 1 day |
| Routine | Scheduled maintenance | Secret rotation, backup verification | per cadence |

## Index

| Runbook | Severity (typical) | Subject |
|---|---|---|
| [secret-rotation](secret-rotation.md) | Routine | Rotate `SESSION_SECRET`, `POSTGRES_PASSWORD`, `WORKSPACE_SECRETS_KEY`, Sentry tokens, deploy SSH key, age recipient |
| [backup-restore](backup-restore.md) | Routine / SEV-1 | Verify backups, manually trigger, restore from a daily/weekly/monthly snapshot |
| [prod-rollback](prod-rollback.md) | SEV-1 | Roll back a bad deploy via revert-and-redeploy or emergency `git reset --hard` on the VPS |
| [migration-recovery](migration-recovery.md) | SEV-1 | Recover from a failed alembic migration; drain mode, point-in-time restore |
| [sentry-triage](sentry-triage.md) | SEV-2/3 | Issue → release → sourcemap workflow for resolving production exceptions |
| [on-call-quickstart](on-call-quickstart.md) | n/a | Alerts, dashboards, escalation, what to do in the first 5 minutes of a page |
| [incident-response](incident-response.md) | SEV-1/2 | Severity triage, comms cadence, post-mortem template |
| [smtp-outage](smtp-outage.md) | SEV-2 | Signup verification + invitation emails fail; degraded paths |
| [provider-outage](provider-outage.md) | SEV-2 | DigiKey or Mouser API unavailable; user-visible impact and mitigation |
| [workspace-recovery](workspace-recovery.md) | SEV-2 | Restore a disabled workspace, audit a suspected isolation leak |

## Where dashboards live

- **Sentry**: <project URL — TODO(verify): fill from `SENTRY_PROJECT` env in deploy job>
- **UptimeRobot**: <monitor URL — TODO(verify)>
- **GitHub Actions**: https://github.com/<org>/stockManager/actions
- **VPS** (SSH): `ssh deploy@<vps-host>` — host in `docs/deployment.md`
- **Healthchecks.io** (backup heartbeat): <project URL — TODO(verify): from `BACKUP_HEALTHCHECK_OK_URL`>
