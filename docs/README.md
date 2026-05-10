# Stock Manager — Documentation

Audience: anyone

Pick the shelf for who you are and what you're doing.

## I'm new here — orient me

Read in this order. They are the canonical source of truth and are kept current.

1. [`ARCHITECTURE.md`](ARCHITECTURE.md) — cold-start: stack, repo layout, ledger model, workspace isolation, API envelope, domain decomposition, migrations, frontend conventions.
2. [`development.md`](development.md) — local dev loop and how to run tests.
3. [`deployment.md`](deployment.md) — prod architecture, CI/CD, ops, backups.

The single most load-bearing file is `ARCHITECTURE.md`. Don't restate things from there elsewhere — link.

## I'm an engineer working on the code

| What you need | Go to |
|---|---|
| What endpoints exist + their shapes | [`api/`](api/README.md) |
| Entities, relationships, invariants | [`domain/`](domain/README.md) |
| Frontend conventions, lib catalog | [`frontend/`](frontend/README.md) |
| Why a piece of code looks the way it does | [`adr/`](adr/README.md) |
| Per-feature rationale (Phases 1–13) | [`phases/`](phases/) |
| Module-by-module orientation | `backend/app/domain/*/README.md`, `web/src/{lib,components,routes}/README.md` (in-tree) |

## I'm on-call or running ops

| Scenario | Runbook |
|---|---|
| Index of all runbooks + severity matrix | [`runbooks/README.md`](runbooks/README.md) |
| Routine secret rotation | [`runbooks/secret-rotation.md`](runbooks/secret-rotation.md) |
| Backup / restore (vps-backup) | [`runbooks/backup-restore.md`](runbooks/backup-restore.md) |
| Prod rollback | [`runbooks/prod-rollback.md`](runbooks/prod-rollback.md) |
| Failed migration recovery | [`runbooks/migration-recovery.md`](runbooks/migration-recovery.md) |
| Sentry triage | [`runbooks/sentry-triage.md`](runbooks/sentry-triage.md) |
| On-call quickstart | [`runbooks/on-call-quickstart.md`](runbooks/on-call-quickstart.md) |
| Incident response | [`runbooks/incident-response.md`](runbooks/incident-response.md) |
| SMTP outage | [`runbooks/smtp-outage.md`](runbooks/smtp-outage.md) |
| DigiKey / Mouser provider outage | [`runbooks/provider-outage.md`](runbooks/provider-outage.md) |
| Workspace recovery | [`runbooks/workspace-recovery.md`](runbooks/workspace-recovery.md) |

## I'm an end user of Stock Manager

| Task | Help page |
|---|---|
| Sign up, verify email, set up first workspace | [`user/getting-started.md`](user/getting-started.md) |
| Add and manage parts | [`user/parts.md`](user/parts.md) |
| Scan bag codes to bulk-import stock | [`user/scan-import.md`](user/scan-import.md) |
| Add, remove, move stock | [`user/stock.md`](user/stock.md) |
| Storage locations | [`user/storage.md`](user/storage.md) |
| Purchase orders + receiving | [`user/orders.md`](user/orders.md) |
| Projects and BOM | [`user/projects-and-bom.md`](user/projects-and-bom.md) |
| Builds — consume stock against a BOM | [`user/builds.md`](user/builds.md) |
| Reports | [`user/reports.md`](user/reports.md) |
| Alerts | [`user/alerts.md`](user/alerts.md) |
| Workspace members and roles | [`user/workspace-management.md`](user/workspace-management.md) |
| Account, password, theme | [`user/account.md`](user/account.md) |

## I'm contributing to docs

Read [`STYLE.md`](STYLE.md) first. Every doc page conforms to it; deviation needs a reason.

## Map of this directory

```
docs/
  README.md          ← you are here
  STYLE.md           doc style guide
  ARCHITECTURE.md    canonical architecture
  development.md     canonical dev loop
  deployment.md      canonical prod / ops
  api/               REST reference (engineer)
  domain/            entity / data model reference (engineer)
  frontend/          frontend dev guide (engineer)
  adr/               architecture decision records (engineer)
  phases/            per-feature rationale (engineer)
  runbooks/          ops scenarios (on-call)
  user/              end-user help (operator)
```
