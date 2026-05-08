# Routing

Audience: engineer

The route tree, the lazy-chunk boundaries that define each network round-trip,
and the deep-link / 401-redirect dance.

## Provider stack

Wired in `web/src/main.tsx` and `web/src/App.tsx`. Order matters — every layer
below depends on the one above.

```tsx
// web/src/main.tsx:51-74
<Sentry.ErrorBoundary fallback={…}>
  <ThemeProvider>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
    <ThemedToaster />
  </ThemeProvider>
</Sentry.ErrorBoundary>
```

`App` itself adds `<AuthProvider>` → `<ConfirmDialogProvider>` →
`<ChunkLoadErrorBoundary>` → `<Suspense>` → `<Routes>` (`web/src/App.tsx:171-282`).

## Route tree

Defined inline in `web/src/App.tsx:175-276`. Three top-level branches:

| Path | Element | Notes |
|---|---|---|
| `/login` | `<Login>` wrapped in `<RedirectIfAuthed>` | Eager-loaded (no Suspense flash) |
| `/signup` | `<Signup>` wrapped in `<RedirectIfAuthed>` | Eager-loaded |
| `/verify` | `<Verify>` | Pre-auth landing for SEC2-014 email verification |
| `/*` | All authed routes, mounted as children of a single `<Gate>` layout route | See below |

### The `<Gate>` layout route

`web/src/App.tsx:118-131`. Mounted **once** as a layout route (no
`element={<Gate><X/></Gate>}` per route — that pattern remounted `AppShell`
on every navigation, fixed in FE CRIT-1). `<AppShell>` survives every authed
navigation, so its mobile drawer state, command-palette state, and in-flight
React-Query requests are not torn down on URL changes.

### Authed routes

All under `<Gate>` (`App.tsx:183-276`). Grouped:

- `/` → `<Navigate to="/parts" replace />` (`App.tsx:184`)
- `/parts`, `/parts/archived`, `/parts/create`, `/parts/scan-import`,
  `/parts/lots`, `/parts/stock/history` — eager (`App.tsx:186-196`)
- `/parts/scan` → `<Navigate to="/parts/scan-import" replace />` —
  legacy redirect for external links (`App.tsx:193`)
- `/parts/:partId` with nested tabs (`info`, `specs`, `sourcing`,
  `authorized-supply`, `stock`, `add`, `remove`, `move`, `history`,
  `lots`, `substitutes`, `members`, `settings`, `other`, `attachments`,
  `activity`) — eager
  (`App.tsx:198-215`). The index route `<Navigate to="info" replace />` lands
  bare `/parts/:partId` on the Info tab.
- `/storage`, `/storage/:storageId/{info,history,settings,other}` — eager
  (`App.tsx:217-226`)
- `/lots/:lotId/{info,move,adjust,history}` — eager (`App.tsx:228-234`)
- `/orders/*`, `/builds/*`, `/projects/*`, `/reports/*`, `/settings/*` —
  **lazy** (see below)
- `*` → `<NotFound />` (`App.tsx:275`)

## Lazy-chunk boundaries

The eager / lazy split is in `App.tsx:9-107`. Rule of thumb: every authed
session lands on `/parts`, so the entire Parts area plus its sibling
read-mostly pages (Storage, Lots) is in the main chunk. Heavier sections
that pull in their own data-grid + filter machinery are split:

```ts
// web/src/App.tsx:69-105
const OrdersList = lazy(() => import("@/routes/orders/OrdersList"));
const BuildsList = lazy(() => import("@/routes/builds/BuildsList"));
const ReportsLayout = lazy(() =>
  import("@/routes/reports/Reports").then(m => ({ default: m.default }))
);
const ProjectsList = lazy(() => import("@/routes/projects/ProjectsList"));
const Account = lazy(() => import("@/routes/settings/Account"));
```

Reports is one source module exporting layout + four sub-reports; the named
exports get wrapped in shims because `lazy()` needs a default export
(`App.tsx:81-95`). All four sub-reports ship together as one `Reports` chunk;
the shims just hand them out per-route.

### Per-route Suspense

Each lazy `<Route element>` is its own Suspense boundary via the `<LazyRoute>`
helper (`App.tsx:165-167`). The fallback is `<RouteSkeleton variant="table" />`
(`App.tsx:155`) — a content-shaped pulse so the page doesn't reflow when the
chunk lands.

Why per-route, not one boundary at the top: `react-router-dom` v6 requires
every direct child of `<Routes>` to be a `<Route>` or `<React.Fragment>`, so
wrapping `<Route>` in `<Suspense>` directly inside `<Routes>` is an invariant
violation. The Suspense has to live inside the route's `element`.

### Chunk-load error recovery

A deploy can make the browser's cached `index.html` reference a chunk hash
that no longer exists. `<ChunkLoadErrorBoundary>` (`App.tsx:173`,
implementation in `web/src/components/ChunkLoadErrorBoundary.tsx`) catches
`ChunkLoadError` / "Failed to fetch dynamically imported module" and:

1. First failure at this `pathname`: stamps a sessionStorage flag and
   `window.location.reload()` to re-fetch the manifest.
2. Second failure (flag already set): shows a retry banner instead of
   looping the reload.

Non-chunk errors are re-thrown so the outer `<Sentry.ErrorBoundary>` still
captures them (`ChunkLoadErrorBoundary.tsx:43-49` for the matcher).

## Deep-link redirect (`<Gate>` ↔ `/login`)

Two-way preservation via `location.state.from`.

### Going to `/login` after a 401

`web/src/App.tsx:118-131`. When an unauthenticated user hits a protected
route, `<Gate>` does:

```tsx
// web/src/App.tsx:125
if (!me) return <Navigate to="/login" replace state={{ from: location }} />;
```

The same handoff happens for sessions that expire mid-use: the auth bus
(see [auth-flow](auth-flow.md)) catches a 401 from any query/mutation and
`<AuthProvider>` calls `nav("/login", { replace: true, state: { from: location } })`
(`web/src/lib/auth.tsx:159`).

### Coming back from `/login` to the original page

`<RedirectIfAuthed>` reads `location.state.from` and bounces the just-signed-in
user back there:

```tsx
// web/src/App.tsx:143-156
const from = (location.state as { from?: Location } | null)?.from;
if (from && from.pathname !== "/login" && from.pathname !== "/signup") {
  return (
    <Navigate
      to={{ pathname: from.pathname, search: from.search, hash: from.hash }}
      replace
    />
  );
}
return <Navigate to="/parts" replace />;
```

The `pathname !== "/login"` guard is the loop-breaker (FE2-019). Default
landing is `/parts`. `search` and `hash` are carried through so deep
links like `/parts/scan-import?storage_id=abc&tab=queue` survive the
auth round-trip (PR #311, issue #304).

## Entry redirects

| Trigger | Source | Behaviour |
|---|---|---|
| Bare `/` | `App.tsx:184` | `<Navigate to="/parts" replace />` |
| Bare `/parts/:partId` | `App.tsx:199` | `<Navigate to="info" replace />` (Info tab) |
| Bare `/storage/:storageId` | `App.tsx:221` | `<Navigate to="info" replace />` |
| Bare `/lots/:lotId` | `App.tsx:229` | `<Navigate to="info" replace />` |
| Bare `/projects/:projectId` | `App.tsx:264` | `<Navigate to="data" replace />` |
| Bare `/reports` | `App.tsx:254` | `<LowStockReport />` as the index route |
| `/parts/scan` | `App.tsx:193` | `<Navigate to="/parts/scan-import" replace />` (legacy) |

All use `replace` so the browser back button doesn't trap the user on the
intermediate URL.

## Workspace switch navigation

`switchWorkspace` in `web/src/lib/auth.tsx:93-110` lands the user on `/parts`
after switching:

```tsx
// web/src/lib/auth.tsx:107
nav("/parts", { replace: true });
```

A full reload was tried first (FE2-002) — it tore down WebSocket and scanner
state for no real benefit. The router-only nav keeps `<AppShell>` mounted
and the cache cleared (see [auth-flow](auth-flow.md)).

## TODO(verify)

- The `/verify` route element (`App.tsx:179`) is rendered **without**
  `<Gate>`, so it's pre-auth. Confirm whether SEC2-014 also wants it bounced
  away from already-authed sessions — currently `RedirectIfAuthed` only
  wraps `/login` and `/signup`.
