# Sourcing API

Audience: engineer

TrustedParts sourcing endpoints for workspace-scoped connection checks.

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. Mounted at `/api/workspaces`; current routes require a cookie session and current workspace.

## Routes

### `POST /api/workspaces/current/sourcing/test`

Probe the current workspace's TrustedParts credentials with a deterministic single-token search.

**Request**

No body.

**Response** - `200 OK` (envelope: `{ data, status }`)

```json
{
  "data": { "ok": true, "message": "OK", "latency_ms": 42 },
  "status": { "category": "ok", "message": "OK" }
}
```

When TrustedParts is not configured or rejects the probe, the HTTP status remains `200 OK` and `data.ok` is `false`.

```json
{
  "data": { "ok": false, "message": "invalid credentials", "latency_ms": 7 },
  "status": { "category": "ok", "message": "OK" }
}
```

**Errors**

- `403 Forbidden` - caller is not an admin of the current workspace.
- `429 Too Many Requests` - more than six probes per minute for the workspace.

**Notes**

- The route decrypts only the current workspace's `sourcing_company_id_enc` and `sourcing_api_key_enc`, then passes plaintext directly to `TrustedPartsClient`; the plaintext credentials are not serialized or logged.
- Probe token: `TEST_PROBE_DO_NOT_BUY`; called with `use_cached_data=false`.
- Friendly failure messages: `not configured`, `invalid credentials`, `rate limited by TrustedParts`, `timeout reaching TrustedParts`, `TrustedParts upstream error`.
- Source: `backend/app/api/routes/sourcing.py:33-78`.
