# Catalog API

Audience: engineer

Public, token-gated, read-only catalog of `published=true` parts. Mounted at `/catalog` (NOT `/api/catalog`) and intentionally outside the workspace member gate (`backend/app/main.py:394-396`). Anyone with the token URL can read.

## Conventions

See [API conventions](./README.md) for envelope, errors. The HTML route returns plain HTML; the JSON route returns a standard `{ data, status }` envelope. Token management lives in [workspaces](./workspaces.md#catalog-tokens).

Tokens are looked up exclusively against `workspace_catalog_tokens` (with `revoked_at IS NULL`); the legacy `Workspace.catalog_token_hash` column is intentionally NOT consulted at lookup time so rotated/revoked tokens cannot authenticate (SEC2-019, `catalog.py:82-92`).

The lookup is constant-time: HMAC the candidate, then `WHERE token_hmac == digest` (`catalog.py:93-104`). A disabled workspace is indistinguishable from a wrong token (no enabled/disabled oracle, `catalog.py:78-80`).

## Response headers

Every catalog response carries (`catalog.py:33-43`):

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: same-origin
Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'none'; frame-ancestors 'none'
Permissions-Policy: ()
```

## Routes

### `GET /catalog/{token}`

Render the catalog as a self-contained HTML page (no JS, inline CSS, `noindex,nofollow`).

**Path**

| Field | Type | Notes |
|---|---|---|
| `token` | string | Plaintext catalog token. |

**Response** — `200 OK`, `text/html`. Lists `published=true`, non-archived parts of the resolved workspace ordered by `name`. Columns: Name, Manufacturer, MPN, Footprint, Description.

**Errors** — `404 catalog.not_found` — empty token, unknown token, revoked token, or workspace `catalog_enabled=False` (`catalog.py:94-95`, `catalog.py:121`).

**Notes**

- Rate limit: `60/minute` per token-prefix bucket (`catalog:{token[:16]}`) AND `120/minute` per IP — defence in depth (`catalog.py:220-221`, `_token_key` at `:46-59`).
- Updates `last_used_at` and `last_used_ip` on the matching `WorkspaceCatalogToken` row (best-effort, `catalog.py:108-118`).
- Source: `backend/app/api/routes/catalog.py:219-229`.

### `GET /catalog/{token}/parts.json`

JSON variant of the catalog. Same gating, same rate limits, same headers.

**Response** — `200 OK`

```json
{ "data": {
    "workspace": { "id": "…", "name": "…" },
    "parts": [ { "id": "…", "name": "…", "manufacturer": "…", "mpn": "…", "footprint": "…", "description": "…" } ]
}, "status": { … } }
```

**Errors** — `404 catalog.not_found`.

**Notes**

- Same `_published_parts` filter (`workspace_id == ws.id AND archived_at IS NULL AND published IS TRUE`, sorted `name`) as the HTML route (`catalog.py:124-133`).
- Source: `backend/app/api/routes/catalog.py:232-244`.
