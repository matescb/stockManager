# Sentry Tunnel API

Audience: engineer

Same-origin proxy for Sentry envelopes so ad-blockers don't drop SDK events. The browser configures `Sentry.init({ tunnel: "/api/sentry-tunnel" })` and POSTs envelopes here; the server validates and forwards to the Sentry ingest endpoint.

Reference: https://docs.sentry.io/platforms/javascript/troubleshooting/#using-the-tunnel-option

## Conventions

See [API conventions](./README.md) for envelope, errors. Mounted at `/api/sentry-tunnel` (the prefix is `/api`, route path is `/sentry-tunnel`; `backend/app/main.py:382`). Not gated by workspace membership — the SDK fires from the login screen too (see the `app/main.py:379-381` comment).

## Routes

### `POST /api/sentry-tunnel`

Forward a Sentry envelope upstream.

**Request** — Sentry envelope bytes (`Content-Type: application/x-sentry-envelope`, but we don't enforce on input).

**Response — no DSN configured** — `204 No Content`. Returned when neither `VITE_SENTRY_DSN` nor `SENTRY_DSN` is set, so SDK retries don't hammer the route (`sentry_tunnel.py:64-68`).

**Response — happy path** — Sentry's response body, status, and `Content-Type` are forwarded verbatim so the SDK sees the real outcome (rate limits, errors) (`sentry_tunnel.py:135-141`).

**Errors**

- `413 sentry_tunnel.too_large` — envelope exceeds `SENTRY_TUNNEL_MAX_BYTES` (200 KiB default). Body includes `max_bytes`. The body is streamed and the running total is checked per chunk so an oversize payload is rejected before buffering (`sentry_tunnel.py:74-85`).
- `400 sentry_tunnel.empty` — zero-byte envelope (`sentry_tunnel.py:89-94`).
- `400 sentry_tunnel.malformed_header` — first line is not valid JSON / UTF-8 (`sentry_tunnel.py:100-108`).
- `400 sentry_tunnel.missing_dsn` — envelope header has no `dsn` key (`sentry_tunnel.py:109-115`).
- `403 sentry_tunnel.dsn_mismatch` — `(host, project_id)` parsed from the envelope's `dsn` is not in the server's allow-list (`sentry_tunnel.py:116-124`).

**Notes**

- Rate limit: `60/minute` per IP (`sentry_tunnel.py:62`).
- Allow-list is `(host, project_id)` tuples derived from `VITE_SENTRY_DSN` and `SENTRY_DSN` (`sentry_tunnel.py:39-58`).
- Upstream POST: `https://{host}/api/{project_id}/envelope/` with `Content-Type: application/x-sentry-envelope`, 10s timeout (`sentry_tunnel.py:126-134`).
- Source: `backend/app/api/routes/sentry_tunnel.py:61-141`.
