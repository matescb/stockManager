# ADR-0011: Session cookie `secure` gated on `APP_ENV == "prod"`

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

A session cookie marked `Secure` is only sent by the browser over HTTPS. In prod, where the host's Apache terminates TLS in front of the stack, that's the right setting — a `Secure` cookie means "never leak this in plaintext, even if the site is misconfigured to also serve HTTP".

In dev, the stack runs over plain HTTP on `localhost:5173` (vite) and `localhost:8000` (uvicorn). A `Secure` cookie set by the dev backend would be dropped by the browser on every subsequent HTTP request, breaking session round-trip and leaving every dev login looking instantly logged-out.

The naive fix — make `Secure` unconditional — breaks dev. The naive other fix — make `Secure` opt-in via a separate env var — adds a way to misconfigure prod.

## Decision

The `secure` flag on the session cookie is bound to the env: `secure=settings().APP_ENV == "prod"` (`backend/app/api/routes/auth.py:49`). It is set by `_set_session_cookie` (`backend/app/api/routes/auth.py:41-50`), which also sets `httponly=True` and `samesite="lax"`.

`APP_ENV=prod` is set in `docker-compose.prod.yml`; it is unset (or `dev`) in `docker-compose.dev.yml` and local pytest. The gate is one expression, not configurable via env, so prod cannot accidentally ship with `secure=False`.

## Consequences

- **Good**: Dev round-trips the cookie over HTTP without ceremony; prod refuses to leak it over HTTP. The decision is encoded in code, not env, so a stray `SECURE_COOKIES=false` in `.env.prod` cannot disable it.
- **Trade-offs**: A future deploy environment that's neither "prod" nor "dev" (a public staging instance over HTTPS, say) would need either an env-name change or this gate amended. Today there is no staging, so it's not yet a problem.
- **What it forbids**:
  - Don't make `secure` unconditional (`secure=True` literal). Local dev runs over HTTP and the cookie won't round-trip.
  - Don't make `secure` configurable via a separate env var. The env-name gate is the one source of truth and has the property that prod cannot accidentally disable it.
  - Don't lower `samesite` from `"lax"` or drop `httponly=True`; both are part of the cookie's defence-in-depth.
  - Don't add a different cookie (CSRF token, remember-me) without applying the same `APP_ENV == "prod"` gate to its `secure` flag.

## Alternatives considered

- **`SECURE_COOKIES` env variable, defaulting to true** — rejected because prod can be misconfigured to false (whereas the current gate cannot).
- **Detect the request scheme and set `secure` per request** — rejected because the cookie is set once on login and re-validated on every subsequent request; per-request scheme detection would also have to handle the proxied case (`X-Forwarded-Proto`) correctly, adding parsing surface that the env gate sidesteps.

## References

- Source: `backend/app/api/routes/auth.py:41-50` (`_set_session_cookie`)
- Source: `docker-compose.prod.yml` (`APP_ENV: prod`)
- Rule: `CLAUDE.md:146-149`
