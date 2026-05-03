# ADR-0013: `--timeout-graceful-shutdown` < `stop_grace_period`

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

When `docker compose down` (or a redeploy) stops the backend service, the sequence is:

1. Compose sends SIGTERM to PID 1 (uvicorn).
2. Uvicorn stops accepting new connections and waits up to `--timeout-graceful-shutdown` seconds for in-flight requests to finish.
3. After `stop_grace_period` (compose-side), Compose sends SIGKILL.

If `stop_grace_period` is shorter than uvicorn's `--timeout-graceful-shutdown`, SIGKILL fires before uvicorn finishes its drain — in-flight requests are dropped, and a connection mid-write to Postgres can leave a transaction hanging until the DB notices the disconnection. The drain is a property of having two timeouts in a stack; whichever is shorter wins, and getting the order wrong silently breaks rolling deploys.

The mirror of that — `--timeout-graceful-shutdown` longer than needed — burns redeploy time but doesn't lose work.

INFRA2-014 set the values: uvicorn at 25s, compose at 30s, with a 5s headroom so timer skew doesn't put SIGKILL inside the drain window.

## Decision

In `docker-compose.prod.yml`:

- `stop_grace_period: 30s` on the backend service (`:147`).
- `--timeout-graceful-shutdown 25` in the uvicorn command (`:148`).

The uvicorn timeout must be at least 5 seconds below `stop_grace_period`. The current values are 25 and 30; if either changes, the other moves with it.

## Consequences

- **Good**: A clean redeploy drains in-flight HTTP without 502s. The 5s headroom absorbs Compose's own internal timer overhead so SIGKILL never fires during a legitimate drain.
- **Trade-offs**: Redeploy minimum latency is 30s if any request is still mid-flight. For this app — small traffic, fast endpoints — that's effectively zero.
- **What it forbids**:
  - Don't change `--timeout-graceful-shutdown` to a value within 5 seconds of (or above) `stop_grace_period`. SIGKILL would fire mid-drain.
  - Don't drop `stop_grace_period` from the compose service definition. Default is 10s, which is below the uvicorn drain — every redeploy would SIGKILL.
  - Don't move `--timeout-graceful-shutdown` into an env variable without a corresponding gate that asserts the inequality at compose-render time.

## Alternatives considered

- **Set both equal** — rejected because Compose's stop signal sequence has its own latency between "30s elapsed" and "SIGKILL delivered"; equal values race, and the race ends in SIGKILL.
- **Drop the graceful drain entirely (`--timeout-graceful-shutdown 0`)** — rejected because in-flight writes to Postgres would terminate uncleanly. The 25s window is enough for the longest endpoint to finish.

## References

- Source: `docker-compose.prod.yml:147-148`
- Rule: `CLAUDE.md:156-160` (INFRA2-014)
- Related: ADR-0009 (compose command form)
