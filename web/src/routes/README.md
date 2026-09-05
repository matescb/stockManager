# routes

Audience: engineer

Page tree for the app. Each top-level folder is a feature area; lazy chunks are split per area so a deploy doesn't invalidate every route's bundle.

(The `docs/frontend/` index calls this directory "pages" in plain English; the on-disk name is `routes/` because each entry maps directly to a TanStack Router route.)

## Files

| Path | What |
|---|---|
| `auth/` | Login, signup, accept-invitation, forgot / reset password |
| `parts/` | Parts list + part detail (Specs / Sourcing / Stock / History / Attachments tabs) |
| `stock/` | Stock dashboard, add / move / adjust forms, history |
| `lots/` | Lot list + lot detail (split, history) |
| `orders/` | Orders list + order detail + receive workflow |
| `builds/` | Builds list + build detail (shortage analysis, reserve, consume) |
| `builds/picklist/` | Printable pick sheet (`@media print`, browser print dialog — not the label printer) |
| `projects/` | Projects (BOMs) list + project detail + BOM import wizard |
| `storage/` | Storage location tree CRUD |
| `reports/` | Aggregate reports (low-stock, value, BOM shortage, expiring) |
| `settings/` | Workspace + member + invitation + provider creds + custom fields + tags |
| `codes/` | `/c/:code` scan landing — resolves a printed short code to its object |
| `labels/` | Label template designer (`/settings/label-templates`) + the Print label / batch-print actions the detail pages and lists import |
| `NotFound.tsx` | 404 page |
| `__tests__/` | Route-level tests |

## Routing

Routes are wired in `web/src/App.tsx` and lazy-loaded per area. Deep-link redirects (e.g. `/p/<id>` → `/parts/<id>`) and 401 redirect handling live there too.

## Hard rules (this module)

1. **Every workspace-scoped route key starts with `["ws", wsId, …]`.** Workspace switch invalidates the whole tree. See [tanstack-patterns](../../../docs/frontend/tanstack-patterns.md).
2. **All HTTP goes through `lib/api.ts`** — never `fetch` from a route component.
3. **Lazy chunks wrap their suspense in `RouteSkeleton`** and data in `QueryStateBoundary` for uniform loading + error UX.

## See also

- [routing](../../../docs/frontend/routing.md) — page tree, lazy-chunk boundaries, deep-link redirects
- [api-layer](../../../docs/frontend/api-layer.md) — `lib/api.ts`, envelope, ApiError
- [tanstack-patterns](../../../docs/frontend/tanstack-patterns.md) — query keys, invalidation
- [auth-flow](../../../docs/frontend/auth-flow.md) — `AuthProvider`, workspace switch, 401 bus

## Don't

- Don't add a new top-level route without a lazy boundary — every area is its own chunk.
- Don't put feature-specific reusable components into `web/src/components/` — keep them under the route folder unless a second area starts using them.
- Don't call `fetch` from a page — use `lib/api.ts` so the cookie + envelope + 401 handling are uniform.
