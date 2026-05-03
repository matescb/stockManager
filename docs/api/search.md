# Search API

Audience: engineer

Cross-entity ILIKE search across parts, storage, projects, lots, and orders. Powers the global search bar.

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination. Mounted at `/api/search` (`backend/app/main.py:386`).

## Routes

### `GET /api/search`

Run an ILIKE `%q%` query across five buckets.

**Query**

| Field | Type | Required | Notes |
|---|---|---|---|
| `q` | string | yes | `1 <= len <= 200`. |

**Per-bucket fields searched** (`search.py:37-88`):

| Bucket | Fields |
|---|---|
| `parts` | `name`, `mpn`, `manufacturer`, `internal_part_number`, `description` |
| `storage_locations` | `name`, `description` |
| `projects` | `name`, `description` |
| `lots` | `name`, `serial_number`, `comments` |
| `orders` | `name`, `supplier`, `comments` |

Each bucket is capped at `_BUCKET_LIMIT = 25` rows and the combined total is capped at `_TOTAL_LIMIT = 50` (`search.py:20-21`, `:91-108`).

**Response** — `200 OK`

```json
{ "data": {
    "parts":             [ { "id": "…", "name": "…", "mpn": "…", "manufacturer": "…" } ],
    "storage_locations": [ { "id": "…", "name": "…" } ],
    "projects":          [ { "id": "…", "name": "…" } ],
    "lots":              [ { "id": "…", "name": "…", "part_id": "…" } ],
    "orders":            [ { "id": "…", "name": "…", "status": "…" } ],
    "more_available": true
}, "status": { … } }
```

`more_available: true` when any bucket was truncated OR the combined count exceeded `_TOTAL_LIMIT` (`search.py:91-108`).

**Notes**

- Rate limit: `30/minute` per workspace (`search.py:25`).
- Each bucket fetches `_BUCKET_LIMIT + 1` rows so truncation is detected without a `COUNT` (`search.py:33-35`).
- No archived-row filtering — archived parts / projects / orders / storage all appear if they match. TODO(verify): is this intentional?
- Source: `backend/app/api/routes/search.py:24-119`.
