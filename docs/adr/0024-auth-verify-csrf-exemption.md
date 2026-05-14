# ADR-0024: `/api/auth/verify` is CSRF-exempt

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-14
- **Supersedes**: —
- **Superseded by**: —

## Context

The CSRF middleware rejects state-changing requests whose `Origin` or
`Referer` does not match the configured CORS allow-list. `/api/auth/verify`
is a `POST`, but it is reached from a verification link opened from an email
client. That request often has no browser `Origin` header or has a
mail-client origin, so the normal CSRF check would block legitimate
verification.

The endpoint is pre-auth and consumes a one-time verification secret rather
than relying on an existing session cookie. Signup stores only an
`SESSION_SECRET`-keyed HMAC of the email token, and verification recomputes
the HMAC of the supplied token and compares it in constant time before
creating the user, workspace, and session.

## Decision

Keep `/api/auth/verify` in the CSRF exemption list. The exemption is part of
the email-verification contract and is acceptable only because the route is
guarded by a per-signup random token whose plaintext is never stored.

## Consequences

- **Good**: Email verification works from browser, native mail-client, and
  webmail contexts without depending on their `Origin` / `Referer` behavior.
- **Trade-offs**: `/api/auth/verify` is a state-changing endpoint outside the
  global CSRF origin gate, so its token design and rate limit carry the
  protection.
- **What it forbids**: Do not remove the HMAC token check, persist plaintext
  verification tokens, make verification session-cookie driven, or add new
  CSRF exemptions without a route-specific mitigation and ADR.

## Alternatives considered

- **Require the CSRF origin check for verification** — rejected because
  email-client initiated verification requests do not reliably carry an
  allow-listed origin.
- **Use a double-submit CSRF token for verification** — rejected because
  verification starts before the user has an authenticated browser session
  and already has a purpose-built one-time token.
- **Change verification to `GET`** — rejected because the endpoint creates
  the user, workspace, and session; keeping it as `POST` preserves the
  state-changing semantics.

## References

- Source: `backend/app/main.py:251-274`
- Source: `backend/app/main.py:306-310`
- Source: `backend/app/api/routes/auth.py:56-65`
- Source: `backend/app/api/routes/auth.py:170-184`
- Source: `backend/app/api/routes/auth.py:207-230`
- Related: `backend/app/domain/users/models.py:84-110`
