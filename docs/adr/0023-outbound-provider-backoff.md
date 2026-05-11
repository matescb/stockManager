# ADR-0023: Outbound provider backoff

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-05-11
- **Supersedes**: —
- **Superseded by**: —

## Context

TrustedParts sourcing and the Mouser/DigiKey catalog providers all call external
HTTP APIs from request paths. Inbound slowapi limits do not protect those
outbound calls from upstream rate limits or short outages. Before this decision,
429/503 responses and transient connection failures propagated immediately to
routes or provider lookup results.

The project also has a hard TLS invariant: backend `httpx` clients must not set
`verify=False` or loosen proxy/environment handling. The retry layer therefore
has to sit on normal `httpx` transports with `verify=True`.

## Decision

All provider HTTP calls use a shared retrying transport. It retries only 429,
503, `httpx.ConnectError`, and `httpx.ReadTimeout`, with at most three retries,
base delay 0.5s, cap 8s, and full jitter. `Retry-After` seconds or HTTP-date
headers override jitter, still capped at 8s.

Current provider code is synchronous, so the shared factory returns a sync
`httpx.Client` backed by `RetryingHTTPTransport`. The same module also provides
`RetryingAsyncHTTPTransport` for future async provider clients.

## Consequences

- **Good**: Short provider-side rate limits and 503 blips are absorbed before
  surfacing as user-visible failures.
- **Trade-offs**: A request can spend additional time waiting for upstream
  recovery, bounded by the existing provider timeout per attempt plus capped
  retry sleeps.
- **What it forbids**: Retrying ordinary client/auth errors, retrying 500, adding
  a retry dependency, or changing `verify=True` to bypass TLS verification.

## Alternatives considered

- **Retry in each provider module** — rejected because it would duplicate policy
  and make status/error handling drift across TrustedParts, Mouser, and DigiKey.
- **Use a retry library** — rejected because the policy is small and issue #507
  forbids new dependencies.
- **Retry every 5xx** — rejected because the accepted contract is limited to 503;
  ordinary 500s should still surface as upstream failures.

## References

- Source: `backend/app/domain/sourcing/providers/_retry_transport.py:15-229`
- Source: `backend/app/domain/sourcing/providers/factory.py:13-27`
- Source: `backend/app/domain/sourcing/client.py:67-81`
- Source: `backend/app/domain/parts/providers/mouser.py:16-21`
- Source: `backend/app/domain/parts/providers/digikey.py:45-106`
- Related ADR: [ADR-0008](0008-no-tls-verify-false.md)
- Issue: `https://github.com/matescb/stockManager/issues/507`
