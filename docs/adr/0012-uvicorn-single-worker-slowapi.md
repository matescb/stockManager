# ADR-0012: uvicorn `--workers 1` for slowapi correctness

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

slowapi (the rate-limiter the backend uses) keeps its bucket store in process memory by default. With one uvicorn worker, a "10 requests / minute" limit means 10 requests per minute, full stop. With N workers, each worker has its own bucket store, so the effective limit is N × 10 requests per minute — and which worker handles a given request is round-robin or load-balanced, so a single attacker still gets N× the budget.

That's not just a quantitative drift; it breaks the security property the rate limit is meant to provide (e.g. login lockout, password reset throttling).

## Decision

The backend uvicorn command in `docker-compose.prod.yml:148` includes `--workers 1`. The number is hard-coded — there is no `WORKERS` env variable to bump it — so a future ops change cannot accidentally multiply the rate limit by editing `.env.prod`.

If traffic ever justifies more workers, the prerequisite is to switch slowapi to a shared backend (Redis) first, so the bucket store is global rather than per-process.

## Consequences

- **Good**: Rate limits mean what they say. Login lockout, scan-throttle, and other per-route limits enforce one budget across all clients.
- **Trade-offs**: Single-worker cap means the backend's CPU concurrency is bounded by uvicorn's async loop. The deploy is one VPS with modest traffic, so this hasn't bitten. If it does, the order is: add Redis → switch slowapi storage → bump workers.
- **What it forbids**:
  - Don't change `--workers 1` in `docker-compose.prod.yml` without first switching slowapi to a Redis-backed bucket store.
  - Don't introduce a `WORKERS` env variable pointing at the uvicorn flag; the literal is the safety property.
  - Don't add a second uvicorn process behind a load balancer for the same reason — same per-process bucket-store divergence.

## Alternatives considered

- **Run multiple workers and accept the rate-limit drift** — rejected because rate limits are security-relevant (login lockout especially), not just polite throttling.
- **Switch slowapi to Redis storage now (preemptive)** — rejected for now because adding Redis to the prod stack is a backup, monitoring, and capacity surface for a problem we don't have. Single-worker headroom is sufficient at current traffic.

## References

- Source: `docker-compose.prod.yml:133-148` (worker count, command line)
- Source: `backend/app/core/ratelimit.py` (slowapi config)
- Rule: `CLAUDE.md:150-153`
