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

## Workspace Isolation

Reads filter by both `workspace_id` and `query_hash`, so identical canonical queries in
two workspaces create two cache rows and cannot cross-hit. The unique index is scoped to
`(workspace_id, query_hash)`, not `query_hash` alone.

Sources: `backend/app/domain/sourcing/cache.py:41`,
`backend/alembic/versions/0038_sourcing_cache.py:53`

## Entry Points

| Operation | Entry point | Notes |
|---|---|---|
| Hash query | `app.domain.sourcing.cache::canonical_query_hash` | `json.dumps(..., sort_keys=True, separators=(",", ":"))`. |
| Read/write cache | `app.domain.sourcing.cache::get_or_fetch` | Returns `(response_json, cache_hit)` and upserts on miss. |
| Sweep expired rows | `app.domain.sourcing.cache::sweep_expired` | Deletes rows with `expires_at < now()`. |

Source: `backend/app/domain/sourcing/cache.py:23`
