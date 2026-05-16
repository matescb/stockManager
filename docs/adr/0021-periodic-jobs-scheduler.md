# ADR-0021: Periodic jobs run in a backend-cron sidecar

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-05-08
- **Supersedes**: —
- **Superseded by**: —

## Context

Phase 1 TrustedParts added a workspace-scoped sourcing cache with a seven-day database cap and a 30-minute service TTL. The database check prevents writes whose `expires_at` is more than seven days after `fetched_at`, but it does not delete expired rows. `sourcing.cache.sweep_expired()` exists for cleanup and is not called by any scheduler yet.

The repo already has one ad hoc in-process periodic task: expired session purge is spawned from FastAPI lifespan in `backend/app/main.py:136` and controlled by `SESSION_PURGE_INTERVAL_SECONDS` in `backend/app/core/config.py:34`. That was acceptable as a single lightweight task, but adding every future periodic job to the request process would create hidden coupling to uvicorn lifecycle, graceful shutdown, and worker count.

The next periodic jobs are:

- `sourcing.cache.sweep_expired` for TrustedParts response retention.
- Expired session purge, currently in FastAPI lifespan, when the shared runner exists.
- Phase 5 alert evaluator (TP-503/504).
- Future routine maintenance jobs that need database access but not an HTTP request.

## Decision

Periodic jobs run through a dedicated `backend-cron` sidecar container that invokes a shared backend CLI entry point, for example `python -m app.cli.run_job <job-name>`. The first implementation job is the sourcing cache sweeper. The sidecar uses the same backend image and database settings as `backend`, but it does not serve HTTP and does not run uvicorn.

Each job must be registered in the CLI allow-list with a clear owner, cadence, and idempotency expectations. The sidecar owns scheduling; the FastAPI request process does not grow a second scheduler.

Jobs using this scheduler:

- `sourcing-cache-sweep` — hourly TrustedParts cache retention sweep in `backend-cron`.
- `sourcing-alerts-evaluate` — 15-minute sourcing alert evaluator in `backend-cron-alerts`.
- `session-purge` — expired session cleanup in `backend-cron-sessions`; hourly by default, configurable via `SESSION_PURGE_INTERVAL_SECONDS`, set to `0` to disable.
- `password-reset-purge` — expired password reset request cleanup in `backend-cron-sessions`; hourly by default, configurable via `PASSWORD_RESET_PURGE_INTERVAL_SECONDS`, set to `0` to disable.

## Consequences

- **Good**:
  - Periodic work does not block the request event loop or depend on ASGI lifespan timing.
  - Future uvicorn worker changes cannot accidentally multiply periodic jobs.
  - The CLI entry point is testable locally and can be run manually during incident response.
  - Jobs share application code, config, logging, and database models without opening an HTTP-only maintenance endpoint.
- **Trade-offs**:
  - Production compose gains another long-running service and one more log stream.
  - Local dev has to document how to run a job manually and how to run the sidecar when testing cadence.
  - The scheduler implementation must handle overlap control for any job that might exceed its cadence.
- **What it forbids**:
  - Adding new periodic jobs directly to FastAPI lifespan, APScheduler, host cron, or systemd timers while `backend-cron` exists.
  - Running the same job from multiple schedulers.
  - Creating HTTP endpoints solely so a scheduler can trigger maintenance jobs.

## Alternatives considered

- **APScheduler in the FastAPI process** — rejected because it binds maintenance cadence to uvicorn lifecycle and can block or contend with the request process. The single-worker invariant in ADR-0012 avoids duplicate jobs today, but using it as a scheduler would make future worker changes riskier.
- **`backend-cron` sidecar container** — accepted because it keeps scheduling inside Docker deployment, uses the same image and environment as the backend, and keeps periodic work outside the HTTP process.
- **systemd timer on the VPS** — rejected because the job schedule would live outside the repo and compose stack. That makes review, local testing, and recovery harder, even though the current VPS already uses host cron for backups.

## References

- Source: `backend/app/domain/sourcing/cache.py:73`
- Source: `backend/app/main.py:136`
- Source: `backend/app/core/config.py:34`
- Related ADR: [ADR-0012](0012-uvicorn-single-worker-slowapi.md)
- Related ADR: [ADR-0020](0020-trustedparts-sourcing-provider-split.md)
- Issue: `https://github.com/matescb/stockManager/issues/341`
- Implementation issue: `https://github.com/matescb/stockManager/issues/356`
