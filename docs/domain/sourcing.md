# Sourcing

Audience: engineer

TrustedParts sourcing stores short-lived provider responses and keeps cache reads workspace-scoped.

## Schema Validation

Live TrustedParts responses are validated against the generated Inventory API v2
Pydantic models before the app maps them into public sourcing DTOs. The generated
`InventoryApiResponse` owns the wire-format contract; `SourcingOffer`,
`SourcingDistributor`, and `SourcingSearchRaw` remain the stable app-facing
contract. Malformed HTTP 200 responses raise `SourcingValidationError`, which API
routes surface as 502 envelopes instead of silently returning partial offers.

The generated models keep the TrustedParts swagger's `additionalProperties: false`
as `extra="forbid"`. This is strict everywhere: if TrustedParts adds an
unannounced field, production calls can return 502 until operators refresh
`docs/schemas/trustedparts-v2.json`, run `make regen-tp-models`, and deploy the
regenerated models. Validation logs include only the request hash and error
type/path, never the response body.

TrustedParts authentication is sent via the `X-Api-Key` request header. The
request body no longer includes `ApiKey` or deprecated `CompanyId`; the encrypted
workspace CompanyId column remains for compatibility but is not decrypted or
required for new requests. HTTP 200 responses with `ErrorMessage` become upstream errors, while
`Messages[]` entries are logged at INFO with a `tp_message` tag and do not fail
the search.

## TrustedParts Gap Fields (TPS-4)

The adapter maps the Inventory API v2 fields that were previously dropped into public
DTOs after generated-model validation. Offer DTOs carry `lifecycle_risk`,
`supply_chain_risk`, `is_affected_by_tariff`, `manufacturer_id`, and `specifications`.
Distributor DTOs carry `distributor_id`, `rohs_compliance`, `availability_text`, and
`quantity_multiple`. Price breaks carry `formatted_amount` and `text`. Search outputs
carry `tp_current_date` and `tp_response_time`; the cache stores those response-level
fields alongside the offer JSON so part-detail and search reads keep the same metadata
on cache hits.

TrustedParts marks some fields as ToU-gated. Workspaces without access can receive
`null` for `lifecycle_risk` and `supply_chain_risk`; `specifications` and
`rohs_compliance` default to `[]` so callers can iterate without null checks. No enum
constraints are applied to lifecycle risk, supply chain risk, or availability text.
Quantity multiple is exposed as an integer count.

Sources: `backend/app/domain/sourcing/client.py:285-418`,
`backend/app/domain/sourcing/service.py:951-1007`,
`backend/app/domain/sourcing/schemas.py:19-110`

## Cache Table

`sourcing_cache` stores one response per `(workspace_id, query_hash)`.
The table keeps:

| Column | Notes |
|---|---|
| `workspace_id` | FK to `workspaces.id` with `ON DELETE CASCADE`. |
| `query_hash` | SHA-256 hex of canonical JSON: sorted keys, compact separators. |
| `query_json` | Original query payload used to produce the hash. |
| `response_json` | Raw response payload cached for reuse. |
| `fetched_at` | Time the response was fetched. |
| `expires_at` | Cache expiry; indexed for sweeps. |
| `created_by` | Optional user id for attribution. |

Source: `backend/alembic/versions/0038_sourcing_cache.py:21`

## Seven-Day Cap

TrustedParts responses must not be stored longer than seven days without ECIA consent.
The database enforces this with:

```sql
CHECK (expires_at <= fetched_at + interval '7 days')
```

The helper also caps caller-supplied TTLs before writing the row.
Sources: `backend/alembic/versions/0038_sourcing_cache.py:42`,
`backend/app/domain/sourcing/cache.py:50`

## Purchase Plans

`purchase_plans` stores short-lived optimizer output for one workspace and project:
build quantity, strategy, country/currency hints, optional preferred distributors, and
status. `purchase_plan_lines` stores the per-BOM-line shortage and the selected offer
snapshot the user is reviewing, including distributor, quantity, unit price, MOQ, lead
time, offer URL, and the cached `available_offers` list used to validate user
overrides during conversion.

Plans inherit the TrustedParts seven-day retention cap:

```sql
CHECK (expires_at <= created_at + interval '7 days')
```

The database also pins static strategy and status values with CHECK constraints. Expired
plans are removed by the same sourcing sweep job as cache rows. When a plan is converted,
draft orders become the permanent record; offer URLs and raw offer snapshots stay on the
ephemeral plan rows and are not copied into order comments.

Sources: `backend/alembic/versions/0039_purchase_plans.py:68`,
`backend/app/domain/sourcing/cache.py:95`

## Phase 4 Plan-to-Order Flow

Phase 4 keeps TrustedParts data ephemeral until the user converts a freshly refreshed
purchase plan into draft purchase orders. Conversion creates one draft order per selected
distributor and one order entry per selected plan line. Optional line overrides are
accepted only when the requested distributor, quantity, unit price, and currency match an
offer cached on that `purchase_plan_lines` row. Invalid overrides fail before any order
rows are written. The flow does not receive stock, create lots, or copy provider URLs
into order comments.

```text
BOM entries
  -> shortage_analysis
  -> TrustedParts search/cache
  -> optimizer
  -> purchase_plan + purchase_plan_lines
       status=draft
       selected_url retained only on plan line
  -> refresh
       force_refresh=True
       status=refreshed
       last_refreshed_at=now
  -> convert
       require status=refreshed
       require last_refreshed_at within 10 minutes
       validate overrides against cached available_offers
       group selected lines by distributor
       create draft purchase orders
       create draft order entries
       status=converted
```

Order comments carry only the compliance-safe summary needed for auditability:
TrustedParts purchase plan id, distributor, generation date, and strategy. Entry comments
carry distributor, packaging, lead-time label, and a short plan id. Raw offer URLs remain
on `purchase_plan_lines.selected_url` and are not persisted into `orders.comments` or
`order_entries.comments`.

Source: `backend/app/domain/sourcing/service.py:57`

## Alerts

`sourcing_alerts` stores workspace-scoped, soft-deletable alert definitions for the
Phase 5b evaluator. Each row targets exactly one part or one project and uses
`threshold` as opaque JSONB; the API validates the per-type shape before insert.

| `alert_type` | Scope | Threshold shape |
|---|---|---|
| `stock_below` | part | `{ "qty": int }` |
| `stock_above` | part | `{ "qty": int }` |
| `back_in_stock` | part | `{}` |
| `out_of_authorized_stock` | part | `{}` |
| `price_changed` | part | `{ "delta_pct": Decimal }` |
| `bom_buyable` | project | `{ "build_quantity": int }` |

The database pins the six MVP alert types with a CHECK constraint, enforces one active
target via `(part_id IS NOT NULL) <> (project_id IS NOT NULL)`, and prevents duplicate
active alerts with a partial unique index over workspace, type, target, and threshold.
`cooldown_seconds` defaults to 24 hours and has a 60-second minimum. Evaluator state
lives on `last_checked_at`, `last_notified_at`, and `last_evaluation_state`.

Source: `backend/alembic/versions/0044_sourcing_alerts.py:20`

### Evaluator Behaviour

`evaluate_all_alerts(db)` scans enabled, non-archived rows across workspaces, dispatches
by `alert_type`, persists `last_checked_at` and `last_evaluation_state`, and commits
per alert row. Evaluator failures roll back only the current row and are logged so later
alerts still run. Source: `backend/app/domain/sourcing/alerts_evaluator.py:42-88`

All evaluator queries stay workspace-scoped. Part and project targets are loaded with
`workspace_id == workspace.id`, recipient lookup joins `workspace_members` on the same
workspace, and sourcing calls receive the current alert workspace. Sources:
`backend/app/domain/sourcing/alerts_evaluator.py:331-349`,
`backend/app/domain/sourcing/alerts_evaluator.py:377-402`

| `alert_type` | Trigger rule | Persisted state |
|---|---|---|
| `stock_below` | current stock crosses from `>= threshold.qty` to `< threshold.qty` | `{ "qty": int }` |
| `stock_above` | current stock crosses from `<= threshold.qty` to `> threshold.qty` | `{ "qty": int }` |
| `back_in_stock` | authorized stock crosses from zero to positive | `{ "had_stock": bool }` |
| `out_of_authorized_stock` | authorized stock crosses from positive to zero | `{ "had_stock": bool }` |
| `price_changed` | same-currency best authorized unit price changes by at least `threshold.delta_pct` percent | `{ "last_price": str, "last_currency": str }` |
| `bom_buyable` | `can_build_after_purchase >= threshold.build_quantity` after a prior not-buyable state | `{ "is_buyable": bool }` |

First evaluation records state and does not notify because no prior state exists.
Stock alerts read quantity only through `domain/stock/service.py::current_quantity`.
Sourcing-typed part alerts call `sourcing.service.search(..., use_cached_data=True)`;
BOM buyability calls `sourcing.service.source_bom(..., use_cached_data=True)` and uses
the returned capacity computed by `compute_build_capacity`. Sources:
`backend/app/domain/sourcing/alerts_evaluator.py:91-279`,
`backend/app/domain/sourcing/alerts_evaluator.py:405-457`

Cooldown is DB-backed: notification is allowed when `last_notified_at IS NULL` or when
the elapsed wall time is at least `cooldown_seconds`. `last_notified_at` updates only
after a successful email send. SMTP failures and missing recipients are logged at
warning level and do not stop the evaluator loop. Sources:
`backend/app/domain/sourcing/alerts_evaluator.py:292-328`,
`backend/app/core/mail.py:70-102`

## Workspace Isolation

Reads filter by both `workspace_id` and `query_hash`, so identical canonical queries in
two workspaces create two cache rows and cannot cross-hit. The unique index is scoped to
`(workspace_id, query_hash)`, not `query_hash` alone.

Sources: `backend/app/domain/sourcing/cache.py:41`,
`backend/alembic/versions/0038_sourcing_cache.py:53`

## BOM Coverage Flow

`source_bom()` starts from build shortage analysis, resolves main/substitute/meta-member
MPNs, dedupes them, checks the budget per <=50-MPN chunk, searches each MPN through the
workspace-scoped cache, joins offers back onto BOM rows, then derives distributor
coverage and build capacity.

```text
project
  -> shortage_analysis
  -> dedupe MPNs
  -> chunk <= 50
  -> search
       -> per-MPN sourcing_cache
  -> join offers to BOM rows
  -> coverage matrix
  -> build capacity
```

The cache boundary is per MPN inside each chunk: `search()` canonicalises one query per
MPN and calls `get_or_fetch()` with the caller workspace id. Sources:
`backend/app/domain/sourcing/service.py:88-145`,
`backend/app/domain/sourcing/service.py:195-231`

Coverage and capacity consume the same joined BOM rows. Distributor coverage records
which project-entry ids each distributor cannot cover; capacity records the lines that
limit build count before and after purchase. Source:
`backend/app/domain/sourcing/coverage.py:43-184`

## Entry Points

| Operation | Entry point | Notes |
|---|---|---|
| Hash query | `app.domain.sourcing.cache::canonical_query_hash` | `json.dumps(..., sort_keys=True, separators=(",", ":"))`. |
| Read/write cache | `app.domain.sourcing.cache::get_or_fetch` | Returns `(response_json, cache_hit)` and upserts on miss. |
| Sweep expired rows | `app.domain.sourcing.cache::sweep_expired` | Deletes rows with `expires_at < now()`. |

Source: `backend/app/domain/sourcing/cache.py:23`

## FX Conversion

The part-detail Authorized Supply route can request display conversion into the
workspace sourcing currency. TrustedParts still owns the native offer payload:
`unit_price`, `currency`, and native price breaks are preserved. Converted display
fields are added only for rows whose distributor currency differs from the requested
currency.

ECB daily reference rates are cached in `fx_rate_snapshots` with one JSONB snapshot
per UTC date. The table is global, not workspace-owned, because ECB rates are public
reference data and identical for every workspace. The snapshot stores the current daily
rate set only; it is not a normalized per-currency history table and is not used for
price-trend reporting.

Sources: `backend/app/domain/fx/models.py:15`,
`backend/app/domain/fx/rates.py:46`,
`backend/app/api/routes/sourcing.py:534`
