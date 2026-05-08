# Sourcing API

Audience: engineer

TrustedParts sourcing endpoints for workspace-scoped connection checks, short-lived offer search, and part-detail sourcing reads.

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. Connection checks are mounted under `/api/workspaces`; search is mounted under `/api/sourcing`; part reads are mounted under `/api/parts`. Current routes require a cookie session and current workspace.

## Routes

### `GET /api/parts/{part_id}/sourcing`

Return cached TrustedParts offers for one part's MPN.

**Request**

Path: `part_id` is a part UUID in the current workspace.

| Field | Type | Required | Notes |
|---|---|---|---|
| `country` | `string` | No | Two-letter override; falls back to workspace sourcing default. |
| `currency` | `string` | No | Three-letter override; falls back to workspace sourcing default. |
| `in_stock_only` | `boolean` | No | Defaults to `false`. |
| `distributors` | `string[]` | No | Repeatable query param; comma-separated values are also accepted. Falls back to `sourcing_preferred_distributors` when omitted. |

**Response** — `200 OK` (envelope: `{ data, status }`)

```json
{
  "data": {
    "mpn": "STM32F103C8T6",
    "offers": [
      {
        "mpn": "STM32F103C8T6",
        "distributors": [
          { "name": "DigiKey", "stock": 42, "unit_price": 1.23, "currency": "EUR" }
        ]
      }
    ],
    "request_id": "trustedparts-request-id",
    "powered_by": "TrustedParts",
    "fetched_at": "2026-05-08T12:00:00+00:00",
    "cache_hit": false,
    "links": {
      "primary": "https://www.trustedparts.com/",
      "attribution": "https://www.trustedparts.com/en/about"
    },
    "reason": "ok"
  },
  "status": { "category": "ok", "message": "OK" }
}
```

Parts without an MPN return a successful no-network response.

```json
{
  "data": { "offers": [], "reason": "no_mpn", "cache_hit": null },
  "status": { "category": "ok", "message": "OK" }
}
```

**Errors**

- `404 Not Found` — `part_id` is missing or belongs to another workspace.
- `409 Conflict` — `{ "data": null, "status": { "category": "conflict", "message": "sourcing not configured" } }`.
- `422 Unprocessable Entity` — validation envelope for malformed UUID or invalid query parameter lengths.
- `429 Too Many Requests` — workspace rate limit: 60 requests/minute.
- `502 Bad Gateway` — TrustedParts auth, rate-limit, timeout, upstream, or response-shape failure.
- `503 Service Unavailable` — `{ "data": null, "status": { "category": "server_error", "message": "sourcing budget exhausted" } }`.

**Notes**

- The route validates `part_id` with `assert_in_workspace()` before reading the part MPN.
- The local cache is scoped by `workspace_id`; the part-detail route calls sourcing search with `ttl_seconds=1800`.
- The route uses member-or-higher role gating and maps the same sourcing exceptions as `POST /api/sourcing/search`.
- Source: `backend/app/api/routes/sourcing.py:132-220`.
- Service: `backend/app/domain/sourcing/service.py:62-138`.

### `POST /api/parts/{part_id}/sourcing/refresh`

Force a live TrustedParts fetch for one part's MPN and replace the local cache row.

**Request**

Path: `part_id` is a part UUID in the current workspace. No body.

**Response** — `200 OK` (envelope: `{ data, status }`)

Shape matches `GET /api/parts/{part_id}/sourcing`. `cache_hit` is `false` when the upstream fetch succeeds.

```json
{
  "data": {
    "mpn": "STM32F103C8T6",
    "offers": [],
    "request_id": "trustedparts-request-id",
    "powered_by": "TrustedParts",
    "cache_hit": false,
    "reason": "ok"
  },
  "status": { "category": "ok", "message": "OK" }
}
```

**Errors**

- `404 Not Found` — `part_id` is missing or belongs to another workspace.
- `409 Conflict` — `{ "data": null, "status": { "category": "conflict", "message": "sourcing not configured" } }`.
- `422 Unprocessable Entity` — `{ "data": null, "status": { "category": "validation_error", "message": "part has no MPN" } }`.
- `429 Too Many Requests` — workspace rate limit: 6 requests/minute.
- `502 Bad Gateway` — TrustedParts auth, rate-limit, timeout, upstream, or response-shape failure.
- `503 Service Unavailable` — `{ "data": null, "status": { "category": "server_error", "message": "sourcing budget exhausted" } }`.

**Notes**

- The route validates `part_id` with `assert_in_workspace()` before reading the part MPN.
- Refresh calls sourcing search with `use_cached_data=false`, `ttl_seconds=1800`, and `force_refresh=true`; the cache helper still upserts on `(workspace_id, query_hash)`.
- Forced refreshes consume the in-process parts-count budget because they always call TrustedParts unless the hard budget check blocks first.
- Source: `backend/app/api/routes/sourcing.py:214-274`.
- Service: `backend/app/domain/sourcing/service.py:62-142`.
- Cache: `backend/app/domain/sourcing/cache.py:28-72`.

### `POST /api/sourcing/search`

Search TrustedParts for 1-50 exact MPNs using the current workspace's encrypted sourcing credentials.

**Request**

| Field | Type | Required | Notes |
|---|---|---|---|
| `mpns` | `string[]` | Yes | 1-50 non-empty MPNs. |
| `country` | `string` | No | Two-letter override; falls back to workspace sourcing default. |
| `currency` | `string` | No | Three-letter override; falls back to workspace sourcing default. |
| `in_stock_only` | `boolean` | No | Defaults to `false`. |
| `distributors` | `string[]` | No | Falls back to `sourcing_preferred_distributors` when omitted. |
| `use_cached_data` | `boolean` | No | Falls back to `sourcing_use_cached_for_dashboards`; forced true when the request is in degraded budget mode. |

**Response** — `200 OK` (envelope: `{ data, status }`)

```json
{
  "data": {
    "results": [
      {
        "mpn": "STM32F103C8T6",
        "offers": [
          {
            "mpn": "STM32F103C8T6",
            "distributors": [
              { "name": "DigiKey", "stock": 42, "unit_price": 1.23, "currency": "EUR" }
            ]
          }
        ],
        "request_id": "trustedparts-request-id",
        "fetched_at": "2026-05-08T12:00:00+00:00",
        "cache_hit": false
      }
    ],
    "request_id": "trustedparts-request-id",
    "powered_by": "TrustedParts",
    "fetched_at": "2026-05-08T12:00:00+00:00",
    "cache_hit": false,
    "links": {
      "primary": "https://www.trustedparts.com/",
      "attribution": "https://www.trustedparts.com/en/about"
    }
  },
  "status": { "category": "ok", "message": "OK" }
}
```

**Errors**

- `409 Conflict` — `{ "data": null, "status": { "category": "conflict", "message": "sourcing not configured" } }`.
- `422 Unprocessable Entity` — validation envelope when `mpns` is empty, over 50, contains empty strings, or an unknown field is sent.
- `429 Too Many Requests` — workspace rate limit: 60 requests/minute.
- `502 Bad Gateway` — TrustedParts auth, rate-limit, timeout, upstream, or response-shape failure.
- `503 Service Unavailable` — `{ "data": null, "status": { "category": "server_error", "message": "sourcing budget exhausted" } }`.

**Notes**

- The route uses member-or-higher role gating and never decrypts credentials in the handler; decryption happens in `make_sourcing_provider()`.
- Local cache rows are scoped by `workspace_id`, and cache hits do not consume the in-process parts-count budget.
- Source: `backend/app/api/routes/sourcing.py:87`.
- Service: `backend/app/domain/sourcing/service.py:39`.
- Factory: `backend/app/domain/sourcing/factory.py:12`.

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
