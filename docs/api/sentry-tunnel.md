# Sentry Tunnel API

Audience: engineer

Same-origin proxy for Sentry envelopes so ad-blockers don't drop SDK events. The browser configures `Sentry.init({ tunnel: "/api/sentry-tunnel" })` and POSTs envelopes here; the server validates and forwards to the Sentry ingest endpoint.

Reference: https://docs.sentry.io/platforms/javascript/troubleshooting/#using-the-tunnel-option

## Conventions

See [API conventions](./README.md) for envelope, errors. Mounted at `/api/sentry-tunnel` (the prefix is `/api`, route path is `/sentry-tunnel`). Not gated by workspace membership — the SDK fires from the login screen too — but requests must either carry a trusted same-origin `Origin` header or a valid session cookie.

## Routes

### `POST /api/sentry-tunnel`

Forward a Sentry envelope upstream.

**Request** — Sentry envelope bytes (`Content-Type: application/x-sentry-envelope`, but we don't enforce on input).

**Response — no DSN configured** — `204 No Content`. Returned when neither `VITE_SENTRY_DSN` nor `SENTRY_DSN` is set, so SDK retries don't hammer the route.

**Response — happy path** — Sentry's response body, status, and `Content-Type` are forwarded verbatim so the SDK sees the real outcome (rate limits, errors).

**Errors**

- `401 auth.not_authenticated` — request has no trusted `Origin` header and no usable session cookie.
- `413 sentry_tunnel.too_large` — envelope exceeds `SENTRY_TUNNEL_MAX_BYTES` (200 KiB default) or the chunk-count guard. The body is streamed and checked per chunk so abusive payloads are rejected before unbounded buffering.
- `400 sentry_tunnel.empty` — zero-byte envelope.
- `400 sentry_tunnel.malformed_header` — first line is not valid JSON / UTF-8.
- `400 sentry_tunnel.missing_dsn` — envelope header has no `dsn` key.
- `403 sentry_tunnel.dsn_mismatch` — `(host, project_id)` parsed from the envelope's `dsn` is not in the server's allow-list.

**Notes**

- Rate limit: `30/minute` per IP.
- Allow-list is `(host, project_id)` tuples derived from `VITE_SENTRY_DSN` and `SENTRY_DSN`.
- Upstream POST: `https://{host}/api/{project_id}/envelope/` with `Content-Type: application/x-sentry-envelope`, 10s timeout.
- Source: `backend/app/api/routes/sentry_tunnel.py`.
