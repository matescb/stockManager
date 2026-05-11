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

## Provider Transport

TrustedParts, Mouser, and DigiKey outbound calls use the shared retrying provider
transport. It retries only 429, 503, `httpx.ConnectError`, and
`httpx.ReadTimeout`; it does not retry 400/401/403/404/422/500. The retry budget
is three retries with 0.5s base delay, 8s cap, and full jitter. `Retry-After`
seconds and HTTP-date headers are honored, capped to the same 8s maximum.

Retry attempts log at INFO and exhausted retry budgets log at WARNING. The
factory keeps `verify=True` on the underlying transport.

Sources: `backend/app/domain/sourcing/providers/_retry_transport.py:15-229`,
`backend/app/domain/sourcing/providers/factory.py:13-27`,
`backend/app/domain/sourcing/client.py:67-81`,
`backend/app/domain/parts/providers/mouser.py:16-21`,
`backend/app/domain/parts/providers/digikey.py:45-106`,
[ADR-0023](../adr/0023-outbound-provider-backoff.md)

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

## Lead Time

TrustedParts Inventory API v2 does not expose lead time as a structured field. The
bundled schema contains only `/v2/search`; distributor stock includes
`Stock.Availability`, which the app maps to `availability_text`, but there is no
`LeadTime` or equivalent numeric field to populate `lead_time_days`.

`availability_text` is operator-readable free text such as `In Stock` or vendor-specific
shipping language. It is not machine-comparable, so the adapter intentionally leaves
`SourcingDistributor.lead_time_days` as `None` instead of deriving values with regex
heuristics. If structured lead time becomes important, file a TrustedParts feature
request or integrate a provider that exposes it explicitly.

When every candidate has unknown lead time, the optimizer's `fastest_availability`
strategy treats all candidates as tied on lead time and falls through to its existing
deterministic secondary keys: unit price, then the alphabetical candidate key. That
fallback is documented behaviour for TP v2 data, not a sourcing bug.

Sources: `docs/schemas/trustedparts-v2.json`,
`backend/app/domain/sourcing/client.py:373`,
`backend/app/domain/sourcing/optimizer.py:291`

## BOM Cost Totals

Project BOM capacity reports two response-level cost numbers. `total_bom_cost`
sums required quantity times best-offer unit price for every priced row and ignores
on-hand stock. `purchase_to_pay_cost` sums short quantity times best-offer unit
price for priced rows that are not blocking after authorized supply; the deprecated
`purchase_to_pay_cost` field exposes the short-quantity price to pay. Totals use Decimal
math, prefer converted/display prices from BOM FX conversion, and skip rows whose
display currency does not match the selected total currency.

Sources: `backend/app/domain/sourcing/coverage.py:45-120`,
`backend/app/domain/sourcing/coverage.py:123-196`,
`backend/app/domain/sourcing/schemas.py:382-407`

## Coverage Variants

Project BOM coverage returns two combination summaries above the per-distributor
matrix: `lowest_total_price_combo` reuses the purchase-plan optimizer's
`lowest_total_price` strategy for distributor selection, while
`fewest_distributors_combo` finds the smallest distributor set that reaches the
target coverage. Variant totals are the price of covered, purchasable BOM lines
with pricing for that combination; partial coverage does not force the total to
`null`. The total is `null` only when no covered, purchasable line has pricing.
The fewest-distributors search is
exhaustive through 10 distributors, then switches to a deterministic greedy set-cover
heuristic that picks the distributor covering the most remaining lines and breaks ties
by combo cost and distributor name.

The legacy per-distributor coverage matrix remains shortfall-based: a distributor
covers a line when its stock can satisfy `short_by`, and `best_single_distributor`
and `best_two_distributor_combo` use that same definition. Purchase-oriented
variant totals account for MOQ by using the selected quantity
`max(short_by, moq)`, and the fewest-distributors variant only treats an offer as
feasible when stock can satisfy that selected quantity.

Sources: `backend/app/domain/sourcing/coverage.py:216-464`,
`backend/app/domain/sourcing/optimizer.py:69-93`

## BOM Risk Flags

Project sourcing keeps the five original BOM risk flags in order, then appends
TrustedParts gap-field flags. `lifecycle_risk_present` and
`supply_chain_risk_present` fire when TrustedParts sends non-whitespace text for
the corresponding offer field. `tariff_affected` fires only when
`is_affected_by_tariff is True`.

`rohs_non_compliant` evaluates distributor RoHS entries against the target region.
There is no workspace `target_rohs_region` setting yet, so the service hardcodes
`EU`. The flag fires when every distributor either lacks an `EU` entry or has an
`EU` entry whose `is_compliant` value is false. At least one compliant `EU` entry
suppresses the flag.

The Sourcing Risk report reuses the same gap-field flag helper and counts those
flags in its default flag-count sort before applying the existing alphabetical
tie-breaker.

Sources: `backend/app/domain/sourcing/service.py:1322-1386`,
`backend/app/domain/reports/service.py:651-675`

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
| `lifecycle_risk_changed` | part | `{ "must_contain": str \| null, "case_sensitive": bool }` |
| `supply_chain_risk_changed` | part | `{ "must_contain": str \| null, "case_sensitive": bool }` |
| `tariff_status_changed` | part | `{}` |

The database pins alert types with a CHECK constraint, enforces one active target via
`(part_id IS NOT NULL) <> (project_id IS NOT NULL)`, and prevents duplicate active
alerts with a partial unique index over workspace, type, target, and threshold.
`cooldown_seconds` defaults to 24 hours and has a 60-second minimum. Evaluator state
lives on `last_checked_at`, `last_notified_at`, and `last_evaluation_state`. Gap-field
alerts were added by migration 0047, whose downgrade refuses while rows of those types
exist.

Sources: `backend/alembic/versions/0044_sourcing_alerts.py:20`,
`backend/alembic/versions/0047_alerts_add_data_alerts.py:28`

### Evaluator Behaviour

`evaluate_all_alerts(db)` scans enabled, non-archived rows across workspaces, dispatches
by `alert_type`, persists `last_checked_at` and `last_evaluation_state`, and commits
per alert row. Evaluator failures roll back only the current row and are logged so later
alerts still run. Search-backed part alerts are grouped by `(workspace_id,
canonical_query_hash)` before evaluation, so alerts with the same canonical TrustedParts
query share one `sourcing.service.search(..., use_cached_data=True)` result while still
evaluating and dispatching independently. Source:
`backend/app/domain/sourcing/alerts_evaluator.py:90-275`

Alert notification dispatch is intentionally at-most-once. When a triggered alert has
recipients, the evaluator renders the email, writes `last_notified_at`, and commits
before calling SMTP; an SMTP outage logs a WARN and the cooldown suppresses duplicate
sends until the next eligible window. A DB commit failure happens before SMTP and leaves
the alert retryable. Source: `backend/app/domain/sourcing/alerts_evaluator.py:80-90`,
`backend/app/domain/sourcing/alerts_evaluator.py:377-425`

All evaluator queries stay workspace-scoped. Part and project targets are loaded with
`workspace_id == workspace.id`, recipient lookup joins `workspace_members` on the same
workspace, and sourcing calls receive the current alert workspace. Sources:
`backend/app/domain/sourcing/alerts_evaluator.py:428-446`,
`backend/app/domain/sourcing/alerts_evaluator.py:474-499`,
`backend/app/domain/sourcing/alerts_evaluator.py:502-596`

| `alert_type` | Trigger rule | Persisted state |
|---|---|---|
| `stock_below` | current stock crosses from `>= threshold.qty` to `< threshold.qty` | `{ "qty": int }` |
| `stock_above` | current stock crosses from `<= threshold.qty` to `> threshold.qty` | `{ "qty": int }` |
| `back_in_stock` | authorized stock crosses from zero to positive | `{ "had_stock": bool }` |
| `out_of_authorized_stock` | authorized stock crosses from positive to zero | `{ "had_stock": bool }` |
| `price_changed` | same-currency best authorized unit price changes by at least `threshold.delta_pct` percent | `{ "last_price": str, "last_currency": str }` |
| `bom_buyable` | `can_build_after_purchase >= threshold.build_quantity` after a prior not-buyable state | `{ "is_buyable": bool }` |
| `lifecycle_risk_changed` | first matching offer's lifecycle risk string changes; optional `must_contain` filters on the new string | `{ "lifecycle_risk": str \| null }` |
| `supply_chain_risk_changed` | first matching offer's supply-chain risk string changes; optional `must_contain` filters on the new string | `{ "supply_chain_risk": str \| null }` |
| `tariff_status_changed` | first matching offer's tariff status flips among `null`, `true`, and `false` | `{ "tariff": bool \| null }` |

First evaluation records state and does not notify because no prior state exists.
Stock alerts read quantity only through `domain/stock/service.py::current_quantity`.
Sourcing-typed part alerts call `sourcing.service.search(..., use_cached_data=True)`;
BOM buyability calls `sourcing.service.source_bom(..., use_cached_data=True)` and uses
the returned capacity computed by `compute_build_capacity`. Sources:
`backend/app/domain/sourcing/alerts_evaluator.py:91-279`,
`backend/app/domain/sourcing/alerts_evaluator.py:405-457`

`must_contain` defaults to no filter for lifecycle and supply-chain alerts. Matching is
case-insensitive unless `case_sensitive` is true. The fields are free strings from
TrustedParts, so these alerts detect transitions rather than validating against an enum.
Sources: `backend/app/domain/sourcing/schemas.py:236-244`,
`backend/app/domain/sourcing/alerts_evaluator.py:280-357`

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
MPN and calls `get_or_fetch()` with the caller workspace id. The canonical query dict
contains `workspace_id`, `provider`, `mpn`, `country_code`, `currency_code`,
`language_code`, sorted/canonical-cased `distributors`, `in_stock_only`,
`use_cached_data`, and `exact_match`. Sourcing credential rotation, deletion, or
provider deconfiguration purges rows for that workspace and provider in the same
transaction as the workspace settings write. Sources:
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
| Build TrustedParts query | `app.domain.sourcing.cache::sourcing_search_query` | Includes workspace/provider/MPN/locale/distributor/request-shape fields. |
| Read/write cache | `app.domain.sourcing.cache::get_or_fetch` | Returns `(response_json, cache_hit)` and upserts on miss. |
| Purge provider rows | `app.domain.sourcing.cache::purge_provider_cache` | Deletes one workspace/provider scope on credential rotation or deconfiguration. |
| Sweep expired rows | `app.domain.sourcing.cache::sweep_expired` | Deletes rows with `expires_at < now()`. |

Source: `backend/app/domain/sourcing/cache.py:23`

## FX Conversion

The part-detail Authorized Supply route and Project Sourcing BOM coverage route can
request display conversion into the workspace sourcing currency. TrustedParts still owns
the native offer payload: `unit_price`, `currency`, and native price breaks are
preserved. Converted display fields are added only for offers whose distributor currency
differs from the requested currency. BOM coverage and capacity summaries keep their
existing calculations; SX-2 only adds converted offer display fields and response-level
FX status.

ECB daily reference rates are cached in `fx_rate_snapshots` with one JSONB snapshot
per UTC date. The table is global, not workspace-owned, because ECB rates are public
reference data and identical for every workspace. The snapshot stores the current daily
rate set only; it is not a normalized per-currency history table and is not used for
price-trend reporting.

Sources: `backend/app/domain/fx/models.py:15`,
`backend/app/domain/fx/rates.py:46`,
`backend/app/domain/fx/_apply.py:20`,
`backend/app/domain/sourcing/service.py:889`
