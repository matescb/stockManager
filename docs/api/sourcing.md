# Sourcing API

Audience: engineer

TrustedParts sourcing endpoints for workspace-scoped connection checks, short-lived offer search, and part-detail sourcing reads.

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. Connection checks are mounted under `/api/workspaces`; search is mounted under `/api/sourcing`; part reads are mounted under `/api/parts`. Current routes require a cookie session and current workspace.

## Routes

### `GET /api/sourcing/alerts`

List current workspace sourcing alerts.

**Request**

| Field | Type | Required | Notes |
|---|---|---|---|
| `enabled` | `boolean` | No | Filters enabled or disabled alerts. |
| `alert_type` | `string` | No | One of the six MVP alert types in the threshold table below. |
| `part_id` | `uuid` | No | Filters part-scoped alerts. |
| `project_id` | `uuid` | No | Filters project-scoped alerts. |
| `include_archived` | `boolean` | No | Defaults to `false`; archived rows are soft-deleted alerts. |
| `limit` | `integer` | No | Defaults to `100`; min `1`, max `500`. |
| `offset` | `integer` | No | Defaults to `0`; deterministic order is newest first. |

**Response** — `200 OK` (envelope: `{ data, status }`)

```json
{
  "data": [
    {
      "id": "9b7f7d43-6b5c-4f7d-bc0e-44c6d73f0992",
      "alert_type": "stock_below",
      "part_id": "012f2f63-3b2c-45c4-a841-682ec681f508",
      "project_id": null,
      "threshold": { "qty": 10 },
      "enabled": true,
      "archived_at": null
    }
  ],
  "status": { "category": "ok", "message": "OK" }
}
```

**Notes**

- The service filters every query by `workspace_id`; archived rows are excluded unless requested.
- `include_archived=true` is list-only. GET-by-id, PATCH, and DELETE treat archived rows as not found.
- Source: `backend/app/api/routes/sourcing.py:156-178`.
- Service: `backend/app/domain/sourcing/service.py:145-172`.

### `POST /api/sourcing/alerts`

Create a workspace-scoped alert definition.

**Request**

| Field | Type | Required | Notes |
|---|---|---|---|
| `alert_type` | `string` | Yes | See threshold table below. Immutable after create. |
| `part_id` | `uuid` | Conditional | Required for all non-`bom_buyable` alerts. Mutually exclusive with `project_id`. |
| `project_id` | `uuid` | Conditional | Required for `bom_buyable`. Mutually exclusive with `part_id`. |
| `threshold` | `object` | Yes | Validated per `alert_type` in the service layer. |
| `country_code` | `string` | No | Two-letter sourcing filter for `back_in_stock`, `out_of_authorized_stock`, and `price_changed`. Ignored for stock threshold alerts. |
| `currency_code` | `string` | No | Three-letter sourcing filter for `price_changed`. Ignored for stock threshold alerts. |
| `distributor_filter` | `string[]` | No | Sourcing filter for authorized-stock and price alerts. Ignored for stock threshold alerts. |
| `notify_user_ids` | `uuid[]` | No | `null` means default recipients. Non-null values must be active members of the workspace. |
| `cooldown_seconds` | `integer` | No | Defaults to `86400`; minimum `60`. |
| `enabled` | `boolean` | No | Defaults to `true`. |

**Thresholds**

| `alert_type` | Scope | `threshold` |
|---|---|---|
| `stock_below` | part | `{ "qty": 10 }`, integer `qty >= 0`. |
| `stock_above` | part | `{ "qty": 50 }`, integer `qty >= 0`. |
| `back_in_stock` | part | `{}`. |
| `out_of_authorized_stock` | part | `{}`. |
| `price_changed` | part | `{ "delta_pct": 5 }`, decimal `0 < delta_pct <= 100`. V1 is relative-change only; absolute target-price thresholds are out of scope for TP-502. |
| `bom_buyable` | project | `{ "build_quantity": 10 }`, integer `build_quantity >= 1`. |

**Response** — `200 OK` (envelope: `{ data, status }`)

Shape matches one item from `GET /api/sourcing/alerts`.

**Errors**

- `404 Not Found` — target part/project is missing or foreign, or a `notify_user_ids` entry is not an active workspace member.
- `422 Unprocessable Entity` — invalid threshold, invalid target scope, both/neither `part_id` and `project_id`, immutable fields, or malformed body.
- `429 Too Many Requests` — workspace rate limit: 30 creates/minute.

**Notes**

- `bom_buyable` rejects sourcing filters; stock threshold alerts silently drop sourcing filters because they evaluate internal stock only.
- Empty threshold objects are intentionally valid for transition alerts, so validation is mapped per type instead of using a nested discriminator.
- Source: `backend/app/api/routes/sourcing.py:181-199`.
- Service: `backend/app/domain/sourcing/service.py:114-142`.

### `GET /api/sourcing/alerts/{alert_id}`

Return one alert from the current workspace.

**Request**

Path: `alert_id` is a sourcing alert UUID in the current workspace.

**Response** — `200 OK` (envelope: `{ data, status }`)

Shape matches one item from `GET /api/sourcing/alerts`.

**Errors**

- `404 Not Found` — `alert_id` is missing, archived, or belongs to another workspace.

**Notes**

- Source: `backend/app/api/routes/sourcing.py:202-212`.
- Service: `backend/app/domain/sourcing/service.py:175-194`.

### `PATCH /api/sourcing/alerts/{alert_id}`

Patch mutable alert fields.

**Request**

Path: `alert_id` is a sourcing alert UUID in the current workspace. Body accepts the same fields as create except `alert_type`; sending `alert_type` returns `422`.

**Response** — `200 OK` (envelope: `{ data, status }`)

Shape matches one item from `GET /api/sourcing/alerts`.

**Errors**

- `404 Not Found` — `alert_id` is missing, archived, or foreign to the workspace; target part/project or notify user is missing or foreign.
- `422 Unprocessable Entity` — invalid threshold, invalid target scope, `alert_type` in the patch body, or malformed body.

**Notes**

- Source: `backend/app/api/routes/sourcing.py:215-231`.
- Service: `backend/app/domain/sourcing/service.py:197-230`.

### `DELETE /api/sourcing/alerts/{alert_id}`

Soft-delete an alert by setting `archived_at`.

**Request**

Path: `alert_id` is a sourcing alert UUID in the current workspace.

**Response** — `200 OK` (envelope: `{ data, status }`)

Returns the archived alert row.

**Errors**

- `404 Not Found` — `alert_id` is missing, archived, or belongs to another workspace.

**Notes**

- Source: `backend/app/api/routes/sourcing.py:234-244`.
- Service: `backend/app/domain/sourcing/service.py:233-243`.

### `POST /api/projects/{project_id}/sourcing`

Join a project's BOM shortage analysis to TrustedParts authorized-distributor offers.

**Request**

Path: `project_id` is a project UUID in the current workspace.

| Field | Type | Required | Notes |
|---|---|---|---|
| `build_quantity` | `integer` | Yes | Must be at least `1`. |
| `country` | `string` | No | Two-letter override; falls back to workspace sourcing default. |
| `currency` | `string` | No | Three-letter override; falls back to workspace sourcing default. |
| `distributors` | `string[]` | No | Falls back to `sourcing_preferred_distributors` when omitted. |
| `in_stock_only` | `boolean` | No | Defaults to `false`. |
| `use_cached_data` | `boolean` | No | Falls back to `sourcing_use_cached_for_dashboards`; forced true when the request is in degraded budget mode. |

**Response** — `200 OK` (envelope: `{ data, status }`)

```json
{
  "data": {
    "rows": [
      {
        "part_name": "STM32",
        "mpn": "STM32F103C8T6",
        "required": 20,
        "available": 0,
        "short_by": 20,
        "authorized_stock": 60,
        "best_offer": {
          "distributor": "Mouser",
          "unit_price": "0.10",
          "currency": "EUR",
          "moq": 1,
          "lead_time_days": 3
        },
        "est_extended_cost": "2.00",
        "risk_flags": []
      }
    ],
    "powered_by": "TrustedParts",
    "fetched_at": "2026-05-08T12:00:00+00:00",
    "partial": false
  },
  "status": { "category": "ok", "message": "OK" }
}
```

**Errors**

- `404 Not Found` — `project_id` is missing or belongs to another workspace.
- `409 Conflict` — `{ "data": null, "status": { "category": "conflict", "message": "sourcing not configured" } }`.
- `422 Unprocessable Entity` — validation envelope when `build_quantity < 1` or request fields are malformed.
- `429 Too Many Requests` — workspace rate limit: 30 requests/minute.
- `502 Bad Gateway` — TrustedParts auth, rate-limit, timeout, upstream, or response-shape failure.
- `503 Service Unavailable` — `{ "data": null, "status": { "category": "server_error", "message": "sourcing budget exhausted" } }`.

**Notes**

- The route validates `project_id` with `assert_in_workspace()` before calling the sourcing service.
- The service reuses `shortage_analysis()`, `dedupe_mpns()`, `chunk_mpns()`, and per-MPN `search()` cache rows; BOM chunks use `ttl_seconds=600`.
- Decimal prices and extended costs serialize as strings.
- Source: `backend/app/api/routes/sourcing.py:134-194`.
- Service: `backend/app/domain/sourcing/service.py:71-135`.
- Pricing: `backend/app/domain/sourcing/pricing.py:9-40`.

### `POST /api/projects/{project_id}/purchase-plan`

Build and persist a short-lived purchase plan from the current project's sourced BOM.

**Request**

Path: `project_id` is a project UUID in the current workspace.

| Field | Type | Required | Notes |
|---|---|---|---|
| `build_quantity` | `integer` | Yes | Must be at least `1`. |
| `strategy` | `string` | No | One of `lowest_total_price`, `fewest_distributors`, `fastest_availability`, `preferred_first`; defaults to `preferred_first`. |
| `country` | `string` | No | Two-letter override; persisted on the plan. |
| `currency` | `string` | No | Three-letter override; persisted on the plan. |
| `distributors` | `string[]` | No | Distributor filter / preferred order for optimizer decisions. |
| `max_distributors` | `integer` | No | Positive cap used by `fewest_distributors`. |
| `moq_overbuy_cap` | `integer` | No | Positive cap; offers requiring more than `shortage * cap` are ignored. |
| `price_tolerance_pct` | `decimal` | No | Preferred-first tolerance; defaults to `5`. |

**Response** — `200 OK` (envelope: `{ data, status }`)

```json
{
  "data": {
    "id": "9b7f7d43-6b5c-4f7d-bc0e-44c6d73f0992",
    "project_id": "012f2f63-3b2c-45c4-a841-682ec681f508",
    "build_quantity": 2,
    "strategy": "preferred_first",
    "status": "draft",
    "expires_at": "2026-05-15T12:00:00+00:00",
    "distributors_used": ["DigiKey"],
    "est_total_cost": "20.80",
    "worst_lead_time_days": 3,
    "unfilled_count": 0,
    "lines": [
      {
        "mpn_searched": "STM32F103C8T6",
        "required_qty": 20,
        "internal_available_qty": 0,
        "shortage_qty": 20,
        "selected_distributor": "DigiKey",
        "selected_qty": 20,
        "selected_unit_price": "1.04",
        "selected_currency": "EUR",
        "selected_moq": 1,
        "selected_url": "https://www.trustedparts.com/..."
      }
    ]
  },
  "status": { "category": "ok", "message": "OK" }
}
```

**Errors**

- `404 Not Found` — `project_id` is missing or belongs to another workspace.
- `409 Conflict` — sourcing is not configured for the workspace.
- `422 Unprocessable Entity` — invalid strategy, invalid quantity, malformed codes, or unknown fields.
- `429 Too Many Requests` — workspace rate limit: 15 requests/minute.
- `502 Bad Gateway` — TrustedParts auth, rate-limit, timeout, upstream, or response-shape failure.
- `503 Service Unavailable` — sourcing budget exhausted.

**Notes**

- The route validates `project_id` with `assert_in_workspace()` before creating any plan rows.
- Plans are snapshots: each call creates a new `purchase_plans` row and child `purchase_plan_lines` rows.
- `expires_at` is capped to `created_at + 7 days`; refresh and conversion are later Phase-4 endpoints.
- Decimal monetary fields serialize as strings.

### `POST /api/sourcing/purchase-plans/{plan_id}/refresh`

Re-run a purchase plan with fresh TrustedParts offers and replace its plan lines.

**Request**

Path: `plan_id` is a purchase plan UUID in the current workspace. No request body is accepted; refresh uses the strategy and filters persisted on the plan.

**Response** — `200 OK` (envelope: `{ data, status }`)

Shape matches `POST /api/projects/{project_id}/purchase-plan`, with `status` set to `refreshed` and `last_refreshed_at` populated.

```json
{
  "data": {
    "id": "9b7f7d43-6b5c-4f7d-bc0e-44c6d73f0992",
    "strategy": "preferred_first",
    "status": "refreshed",
    "expires_at": "2026-05-15T12:00:00+00:00",
    "last_refreshed_at": "2026-05-09T12:00:00+00:00",
    "lines": []
  },
  "status": { "category": "ok", "message": "OK" }
}
```

**Errors**

- `404 Not Found` — `plan_id` is missing or belongs to another workspace.
- `409 Conflict` — the plan has expired, or sourcing is not configured.
- `422 Unprocessable Entity` — malformed UUID.
- `429 Too Many Requests` — workspace rate limit: 15 requests/minute.
- `502 Bad Gateway` — TrustedParts auth, rate-limit, timeout, upstream, or response-shape failure.
- `503 Service Unavailable` — sourcing budget exhausted.

**Notes**

- Expired plans return `409 Conflict` with `message="plan expired"`.
- Refresh deletes the old `purchase_plan_lines` and inserts a fresh optimizer outcome.
- Refresh does not extend `expires_at`; the original 7-day cap stays in force.
- The service forces a live TrustedParts refresh by using `use_cached_data=false` and bypassing the local cache hit path.

### `POST /api/sourcing/purchase-plans/{plan_id}/orders`

Convert a freshly refreshed purchase plan into draft purchase orders.

**Request**

Path: `plan_id` is a purchase plan UUID in the current workspace. No request body is accepted in this phase.

**Response** — `200 OK` (envelope: `{ data, status }`)

```json
{
  "data": {
    "orders": [
      {
        "id": "af28ff2a-33e6-4f6f-9825-7840c37f4440",
        "name": "TrustedParts purchase — DigiKey — 2026-05-09",
        "supplier": "DigiKey",
        "status": "draft",
        "currency": "EUR",
        "comments": "TrustedParts purchase plan #... — distributor=DigiKey — generated=2026-05-09 — strategy=preferred_first",
        "entries": [
          {
            "part_id": "012f2f63-3b2c-45c4-a841-682ec681f508",
            "quantity_ordered": 25,
            "unit_price": "1.04",
            "currency": "EUR",
            "comments": "TrustedParts: distributor=DigiKey, packaging=cut-tape, lead_time=3d, plan=9b7f7d43"
          }
        ]
      }
    ]
  },
  "status": { "category": "ok", "message": "OK" }
}
```

**Errors**

- `404 Not Found` — `plan_id` is missing or belongs to another workspace.
- `409 Conflict` — the plan has not been refreshed or its refresh is older than 10 minutes.
- `422 Unprocessable Entity` — one distributor group contains mixed currencies.

**Notes**

- The route creates one `Order(status="draft")` per selected distributor and one `OrderEntry` per selected plan line.
- Conversion flips the plan to `converted` in the same transaction.
- Permanent order comments are compliance-safe summaries. The raw `selected_url` from ephemeral plan lines is never copied into `orders.comments` or `order_entries.comments`.

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
          {
            "name": "DigiKey",
            "stock": 42,
            "unit_price": 1.23,
            "currency": "USD",
            "unit_price_converted": "1.1070",
            "currency_displayed": "EUR",
            "fx_converted": true,
            "fx_rate_date": "2026-05-08",
            "price_breaks_converted": [
              { "quantity": 1, "unit_price": "1.1070" }
            ]
          }
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
    "reason": "ok",
    "fx_status": null
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
- When a `currency` query param is supplied, distributor prices that still arrive in another currency are display-converted through the global ECB daily snapshot. Native `unit_price` / `currency` stay unchanged; converted display values are exposed as `unit_price_converted`, `currency_displayed`, `fx_converted`, `fx_rate_date`, and `price_breaks_converted`.
- `fx_status` is `unavailable` when at least one requested conversion could not be produced; affected rows keep native prices.
- The route uses member-or-higher role gating and maps the same sourcing exceptions as `POST /api/sourcing/search`.
- Source: `backend/app/api/routes/sourcing.py:376`.
- Service: `backend/app/domain/sourcing/service.py:595`.
- FX: `backend/app/domain/fx/rates.py:46`.

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
