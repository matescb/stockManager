# Sourcing

Audience: engineer

TrustedParts sourcing stores short-lived provider responses and keeps cache reads workspace-scoped.

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
time, and offer URL.

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
