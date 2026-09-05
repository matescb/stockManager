# API Reference

Audience: engineer

Per-router REST reference for every public endpoint. One file per logical area; routers that share a path prefix are grouped (e.g. `parts.md` covers parts_core, parts_assets, parts_refresh, parts_scan, parts_provider).

## Conventions

### Envelope

Every response is `{ data, status }`. See [ADR-0003](../adr/0003-api-envelope-data-status.md). Server-side use `responses.ok()` / `responses.err()`. Client-side `lib/api.ts` unwraps `data` and throws `ApiError(status, body, msg)` on non-2xx.

### Error body

Non-2xx responses keep the `{ data, status }` envelope with `data: null`. The `status` block carries `category` (a short string like `"part.conflict"`) and `message` (human-readable). When a route raises `HTTPException(detail={"message": "…", **extras})`, `core/responses.py::http_exception_handler` (`backend/app/core/responses.py:95-109`) spreads `extras` onto the **top level** of the response body alongside `data` / `status` — not into `data`. So a 409 from part create looks like:

```json
{ "data": null, "status": { "category": "part.conflict", "message": "…" }, "existing_id": "…", "existing_name": "…" }
```

Specific extras are documented per route.

### Authentication

Cookie-based session. `credentials: "include"` on all client calls. Unauthenticated calls return `401`. Routes that need a workspace context return `403` if the cookie's workspace doesn't match the resource.

### Pagination

Cursor-based on the list endpoints that ship it (parts, lots, stock history). Pattern: `?limit=N&cursor=<opaque>`. Response includes `next_cursor: string | null`. See [ADR-0??] (TODO if pagination warrants its own ADR).

### Rate limiting

slowapi, per-process bucket store, per-IP. The reverse proxy must set `X-Forwarded-For` (handled by uvicorn `--proxy-headers --forwarded-allow-ips=*`). See [ADR-0012](../adr/0012-uvicorn-single-worker-slowapi.md).

## Routes by area

| File | Prefix | Description |
|---|---|---|
| [auth](auth.md) | `/api/auth` | Signup, email verification, login, logout, `me` |
| [workspaces](workspaces.md) | `/api/workspaces` | Workspace CRUD, members, invitations, catalog tokens |
| [invitations](invitations.md) | `/api/invitations` | Accept invitation (no workspace context) |
| [parts](parts.md) | `/api/parts` | parts_core + parts_assets + parts_refresh + parts_scan + parts_provider |
| [categories](categories.md) | `/api/categories` | Part categories + per-category EDA defaults |
| [eda](eda.md) | `/api/eda`, `/api/parts/{id}/eda` | KiCad library CRUD, uploads, vendor-zip + LCSC import, client setup |
| [kicad](kicad.md) | `/kicad-api` | KiCad's own protocols: the HTTP library and the PCM repository. **Not** the envelope |
| [stock](stock.md) | `/api/stock`, `/api/lots` | Add/remove/move/adjust + lot management |
| [storage](storage.md) | `/api/storage` | Storage location CRUD |
| [projects](projects.md) | `/api/projects`, `/api/bom-presets` | Projects, BOM CRUD, BOM import wizard, presets |
| [orders](orders.md) | `/api/orders` | Purchase orders + receive workflow |
| [builds](builds.md) | `/api/builds` | Builds + consume + reservations + shortage analysis |
| [reports](reports.md) | `/api/reports` | Aggregate queries (low-stock, value, BOM shortage, expiring) |
| [sourcing](sourcing.md) | `/api/sourcing`, `/api/workspaces` | TrustedParts connection checks, offer search, part/project sourcing reads |
| [attachments-tags-cf](attachments-tags-cf.md) | `/api/attachments`, `/api/tags`, `/api/custom-fields` | Polymorphic surfaces |
| [codes](codes.md) | `/api/codes` | Universal short codes for scannable objects + the scan resolver |
| [label-templates](label-templates.md) | `/api/label-templates` | Label layouts, the JScript render engine, and test printing |
| [tokens](tokens.md) | `/api/tokens` | Personal access tokens for KiCad / agent access |
| [agents](agents.md) | — | Cross-cutting guide for non-browser clients: token auth, error codes, curl quickstart |
| [mcp](mcp.md) | `/mcp` | MCP server for AI assistants: connecting, the 19 tools, rate limits, read-only semantics |
| [audit](audit.md) | `/api/audit` | Activity log query |
| [search](search.md) | `/api/search` | Cross-entity search |
| [catalog](catalog.md) | `/catalog` | Public token-gated read-only catalog |
| [sentry-tunnel](sentry-tunnel.md) | `/api/sentry-tunnel` | Sentry envelope tunnel for ad-blocker bypass |
