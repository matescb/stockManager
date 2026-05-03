# API Reference

Audience: engineer

Per-router REST reference for every public endpoint. One file per logical area; routers that share a path prefix are grouped (e.g. `parts.md` covers parts_core, parts_assets, parts_scan, parts_provider).

## Conventions

### Envelope

Every response is `{ data, status }`. See [ADR-0003](../adr/0003-api-envelope-data-status.md). Server-side use `responses.ok()` / `responses.err()`. Client-side `lib/api.ts` unwraps `data` and throws `ApiError(status, body, msg)` on non-2xx.

### Error body

Non-2xx responses have `data` set to the dict from `HTTPException(detail=…)`, spread by `core/responses.py::http_exception_handler`. Specific error shapes are documented per route (e.g. `409 Conflict` on part create returns `{ existing_id, existing_name, … }`).

### Authentication

Cookie-based session. `credentials: "include"` on all client calls. Unauthenticated calls return `401`. Routes that need a workspace context return `403` if the cookie's workspace doesn't match the resource.

### Pagination

Cursor-based on the list endpoints that ship it (parts, lots, stock history). Pattern: `?limit=N&cursor=<opaque>`. Response includes `next_cursor: string | null`. See [ADR-0??] (TODO if pagination warrants its own ADR).

### Rate limiting

slowapi, per-process bucket store, per-IP. The reverse proxy must set `X-Forwarded-For` (handled by uvicorn `--proxy-headers --forwarded-allow-ips=*`). See [ADR-0012](../adr/0012-uvicorn-single-worker-slowapi.md).

## Routes by area

| File | Prefix | Description |
|---|---|---|
| [auth](auth.md) | `/api/auth` | Signup, login, logout, me, password change |
| [workspaces](workspaces.md) | `/api/workspaces` | Workspace CRUD, members, invitations, catalog tokens |
| [invitations](invitations.md) | `/api/invitations` | Accept invitation (no workspace context) |
| [parts](parts.md) | `/api/parts` | parts_core + parts_assets + parts_scan + parts_provider |
| [stock](stock.md) | `/api/stock`, `/api/lots` | Add/remove/move/adjust + lot management |
| [storage](storage.md) | `/api/storage` | Storage location CRUD |
| [projects](projects.md) | `/api/projects`, `/api/bom-presets` | Projects, BOM CRUD, BOM import wizard, presets |
| [orders](orders.md) | `/api/orders` | Purchase orders + receive workflow |
| [builds](builds.md) | `/api/builds` | Builds + consume + reservations + shortage analysis |
| [reports](reports.md) | `/api/reports` | Aggregate queries (low-stock, value, BOM shortage, expiring) |
| [attachments-tags-cf](attachments-tags-cf.md) | `/api/attachments`, `/api/tags`, `/api/custom-fields` | Polymorphic surfaces |
| [audit](audit.md) | `/api/audit` | Activity log query |
| [search](search.md) | `/api/search` | Cross-entity search |
| [catalog](catalog.md) | `/catalog` | Public token-gated read-only catalog |
| [sentry-tunnel](sentry-tunnel.md) | `/api/sentry-tunnel` | Sentry envelope tunnel for ad-blocker bypass |
