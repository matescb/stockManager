# api/routes

Audience: engineer

Every public REST endpoint lives here, one file per logical area. This README is a router → docs map; the per-route reference is in `docs/api/`.

## Files

| Router file | Prefix | API doc |
|---|---|---|
| `auth.py` | `/api/auth` | [auth](../../../../docs/api/auth.md) |
| `workspaces.py` | `/api/workspaces` | [workspaces](../../../../docs/api/workspaces.md) |
| `invitations.py` | `/api/invitations` | [invitations](../../../../docs/api/invitations.md) |
| `parts_core.py` | `/api/parts` (core CRUD) | [parts](../../../../docs/api/parts.md) |
| `parts_assets.py` | `/api/parts/assets/...` | [parts](../../../../docs/api/parts.md) |
| `parts_scan.py` | `/api/parts/scan/...` | [parts](../../../../docs/api/parts.md) |
| `parts_provider.py` | `/api/parts/provider/...` | [parts](../../../../docs/api/parts.md) |
| `_parts_shared.py` | (helpers, no routes) | — |
| `stock.py` | `/api/stock` | [stock](../../../../docs/api/stock.md) |
| `lots.py` | `/api/lots` | [stock](../../../../docs/api/stock.md) |
| `storage.py` | `/api/storage` | [storage](../../../../docs/api/storage.md) |
| `projects.py` | `/api/projects` | [projects](../../../../docs/api/projects.md) |
| `bom_presets.py` | `/api/bom-presets` | [projects](../../../../docs/api/projects.md) |
| `orders.py` | `/api/orders` | [orders](../../../../docs/api/orders.md) |
| `builds.py` | `/api/builds` | [builds](../../../../docs/api/builds.md) |
| `reports.py` | `/api/reports` | [reports](../../../../docs/api/reports.md) |
| `attachments.py` | `/api/attachments` | [attachments-tags-cf](../../../../docs/api/attachments-tags-cf.md) |
| `tags.py` | `/api/tags` | [attachments-tags-cf](../../../../docs/api/attachments-tags-cf.md) |
| `custom_fields.py` | `/api/custom-fields` | [attachments-tags-cf](../../../../docs/api/attachments-tags-cf.md) |
| `audit.py` | `/api/audit` | [audit](../../../../docs/api/audit.md) |
| `_activity.py` | (helpers, no routes) | — |
| `search.py` | `/api/search` | [search](../../../../docs/api/search.md) |
| `catalog.py` | `/catalog` | [catalog](../../../../docs/api/catalog.md) |
| `sentry_tunnel.py` | `/api/sentry-tunnel` | [sentry-tunnel](../../../../docs/api/sentry-tunnel.md) |

## Conventions

- Every response uses `core/responses.py::ok` / `::err`. Envelope is `{ data, status }`. See [ADR-0003](../../../../docs/adr/0003-api-envelope-data-status.md).
- `core/deps.py::get_current_user` is on every route; `get_current_workspace` is on every route except invitations and the public catalog.
- Workspace isolation is the route author's job. See [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md).
- Rate limiting via slowapi, single-process bucket. See [ADR-0012](../../../../docs/adr/0012-uvicorn-single-worker-slowapi.md).

## See also

- [API index](../../../../docs/api/README.md) — full per-area pages
- [ADR-0003](../../../../docs/adr/0003-api-envelope-data-status.md) — envelope decision

## Don't

- Don't return a bare payload — always wrap with `responses.ok()`. The frontend's `lib/api.ts` will throw on a missing envelope.
- Don't run business logic inside a route handler when there's a `domain/<area>/service.py` — keep routes thin.
- Don't query `stock_entries` directly from a route. Go through `domain/stock/service.py`. See [ADR-0001](../../../../docs/adr/0001-append-only-stock-ledger.md).
