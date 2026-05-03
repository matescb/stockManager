# lib

Audience: engineer

Shared frontend infra: HTTP client, auth context, theme, query keys, mutations helper, formatters, bag-code parser, provider catalog key list, Zod schemas.

## Files

| File | What |
|---|---|
| `api.ts` | `get` / `post` / `patch` / `delete` / `upload`; envelope unwrap; `ApiError(status, body, msg)`; 401 redirect bus; `categoryToUserMessage` |
| `auth.tsx` | `AuthProvider`, `useAuth`, workspace switch, 401 listener |
| `theme.tsx` | `ThemeProvider`, `useTheme` (light / dark / system) |
| `queryKeys.ts` | Canonical key factories — `["ws", wsId, …]` shape |
| `mutations.ts` | `useApiMutation` wrapper + standard invalidation helpers |
| `schemas.ts` | Zod schemas mirroring API responses |
| `format.ts` | Number / date / quantity formatters (locale-aware) |
| `bagCode.ts` | MIL-STD-130N parser + normaliser; produces the same signature the server computes |
| `providerCatalog.ts` | Catalog vs spec custom-field key list (mirror of the server-side list) |
| `cn.ts` | `cn(...)` className combiner |
| `*.test.ts`, `__dom__/`, `__fixtures__/` | Co-located unit / DOM tests + fixtures |

## Public surface

| Operation | Entry point |
|---|---|
| HTTP call | `api.ts::get` / `post` / `patch` / `delete` / `upload` |
| Build a query key | `queryKeys.ts::*` factories |
| Wire a mutation + invalidations | `mutations.ts::useApiMutation` |
| Parse a scanned bag code | `bagCode.ts` (default export / named parser) |
| Classify a key as catalog vs spec | `providerCatalog.ts` |

## Hard rules (this module)

1. **All HTTP goes through `api.ts`.** It sends `credentials: "include"`, unwraps the envelope, and throws `ApiError` on non-2xx. See [api-layer](../../../docs/frontend/api-layer.md).
2. **Query keys start with `["ws", wsId, …]`** so workspace switch invalidates everything tenant-scoped in one call. See [tanstack-patterns](../../../docs/frontend/tanstack-patterns.md).
3. **`bagCode.ts` normalisation order must match `backend/app/domain/parts/services/bag_signature.py`.** The signature is the only stable correlation key for re-scans. See [ADR-0006](../../../docs/adr/0006-bag-signature-normalization.md).
4. **`providerCatalog.ts` must mirror its server twin.** Catalog keys vs user specs split the Specs / Sourcing tabs. See [ADR-0007](../../../docs/adr/0007-provider-catalog-vs-spec-split.md).

## See also

- [api-layer](../../../docs/frontend/api-layer.md) — envelope unwrap, ApiError, `categoryToUserMessage`
- [tanstack-patterns](../../../docs/frontend/tanstack-patterns.md) — query keys, invalidation, `useApiMutation`
- [scanner](../../../docs/frontend/scanner.md) — how `bagCode.ts` plugs into ZXing / Scandit
- [auth-flow](../../../docs/frontend/auth-flow.md) — `AuthProvider`, workspace switch, 401 redirect bus

## Don't

- Don't `fetch` directly from a component — go through `api.ts` so the envelope, cookie, and 401 handling are uniform.
- Don't change `bagCode.ts` normalisation without changing the server side at the same time. The signature changes silently and breaks re-scan.
- Don't add a workspace-scoped query key that doesn't start with `["ws", wsId, …]` — workspace switch won't invalidate it.
