# TanStack patterns

Audience: engineer

How `web/src/` uses TanStack Query: workspace-scoped query keys, the
narrow invalidation helpers, `useApiMutation`, and the auth bus that
piggybacks on the same module.

## Why every key starts with `["ws", workspaceId, …]`

Pre-fix (FE2-003 / FE2-004 / v1 FE HIGH-8) keys looked like `["parts"]`,
`["orders"]`, etc. The backend filters by the cookie session's workspace,
but TanStack's cache is in-memory in the SPA. When a user switched
workspace, the in-memory cache from the **previous** workspace served the
next render before the new refetch landed. That's a multi-tenant data
leak waiting to happen.

The fix has two parts (`web/src/lib/queryKeys.ts:1-28`):

1. Every query key is now prefixed with `["ws", workspaceId, …]`, so
   TanStack treats two workspaces as distinct caches.
2. On workspace switch, `qc.clear()` still runs to free memory
   (`web/src/lib/auth.tsx:102`), but even without it a refetch could not
   read the wrong tenant's data.

## `useWsKey` vs `wsKeyOf`

```ts
// web/src/lib/queryKeys.ts:44-57
export function useWsKey(...rest: unknown[]): unknown[] {
  const { workspaceId } = useAuth();
  return ["ws", workspaceId ?? "none", ...rest];
}

export function wsKeyOf(workspaceId, ...rest): unknown[] {
  return ["ws", workspaceId ?? "none", ...rest];
}
```

The `use` prefix is load-bearing: `useWsKey` reads `useAuth()` so it's
a hook and crashes outside render with "Invalid hook call". Calling it
from an `onClick` or a mutation `onSuccess` is the regression PR #33
shipped on the first pass — the entire mutation surface (archive, move,
add stock, receive order, …) crashed on the first click.

Rule:

- **Inside render**: `useWsKey("parts", id)` — reads workspace from auth.
- **In a callback** (event handler, `onSuccess`, `onError`, …):
  capture `workspaceId` from `useAuth()` at render time, then call
  `wsKeyOf(workspaceId, "parts", id)`.

Pinning test: `web/src/components/__tests__/wsKey-handlers.test.tsx`.
The "useWsKey throws outside render" assertion at line 69 is exactly the
Invalid-hook-call regression; the post-fix call shape is at lines 73-103.

`null` / `undefined` workspaceIds collapse to the literal string
`"none"` (`queryKeys.ts:46`, `:56`) so SSR or pre-bootstrap renders don't
collide with real workspace caches.

`wsScope(workspaceId)` returns `["ws", workspaceId ?? "none"]` —
the workspace-only prefix used for blanket invalidation
(`queryKeys.ts:60-62`).

## Invalidation helpers

`queryKeys.ts:64-156`. Each returns `unknown[][]` so callers loop:

```ts
for (const k of archivePartKeys(workspaceId, partId))
  qc.invalidateQueries({ queryKey: k });
```

Three signatures:

```ts
// web/src/lib/queryKeys.ts:79-89
archivePartKeys(workspaceId, partId): unknown[][]
//  → ["ws", ws, "parts"]
//  → ["ws", ws, "part", partId]
//  → ["ws", ws, "report", "low-stock"]
//  → ["ws", ws, "report", "stock-value"]

// web/src/lib/queryKeys.ts:96-105
archiveStorageKeys(workspaceId, storageId): unknown[][]
//  → ["ws", ws, "storage"]
//  → ["ws", ws, "storage", storageId]
//  → ["ws", ws, "report", "stock-value"]

// web/src/lib/queryKeys.ts:122-143
lotMutationKeys(workspaceId, lot, storageIds = []): unknown[][]
//  Covers parts list, lots list, the lot, the part (prefix-match
//  invalidates :stock, :lots, :history, :custom-fields, …),
//  the three stock-rollup reports, and any storage IDs touched.

// web/src/lib/queryKeys.ts:148-156
archiveProjectKeys(workspaceId, projectId): unknown[][]
//  → ["ws", ws, "projects"]
//  → ["ws", ws, "project", projectId]
```

`lotMutationKeys` is the trickiest one — both move and adjust-count
change a part's total on-hand. Callers must include both source and
destination storage IDs on a move; otherwise the source bin's "what's
in here" view goes stale (`queryKeys.ts:117-126`).

The `["ws", ws, "part", partId]` entry is a prefix match — TanStack
invalidates every sub-key (`:stock`, `:lots`, `:history`,
`:custom-fields`, …) off it, so don't enumerate them separately
(`queryKeys.ts:130-133`).

## Reading queries — the `useWsKey` pattern

Idiomatic call site:

```tsx
// web/src/components/CommandPalette.tsx:48-53
const { data: results } = useQuery({
  queryKey: useWsKey("cp-search", q),
  queryFn: () => api.get<SearchData>(`/search?q=${encodeURIComponent(q)}`),
  enabled: open && q.trim().length >= 2,
  staleTime: 30_000,
});
```

The same key shape is reused across mounts so the cache stays shared
(e.g. the scanner's `useWsKey("ws", "current")` matches the Settings →
Workspace page's read of the same shape — `web/src/components/scanner/Scanner.tsx:43-46`).

## Display-cache reads

Use per-query display-cache options when a read is expensive enough that
showing a full skeleton on every remount harms the workflow, and the key
already contains every filter that changes the response. Project Sourcing's
BOM coverage query is the reference case (`web/src/routes/projects/sourcing/ProjectSourcingPage.tsx:710-719`).

```tsx
// web/src/routes/projects/sourcing/ProjectSourcingPage.tsx
const query = useQuery({
  queryKey: useWsKey("sourcing", "project", projectId, requestBody),
  queryFn: ({ signal }) => api.post(path, requestBody, { signal }),
  staleTime: 5 * 60 * 1000,
  gcTime: 30 * 60 * 1000,
  placeholderData: previousData => previousData,
  refetchOnWindowFocus: false,
});
```

- `staleTime` is the trust window. Within it, remounts render from memory
  without a network request.
- `gcTime` is the retention window after the last component unmounts.
- `placeholderData: previousData => previousData` is the TanStack Query 5
  replacement for v4 `keepPreviousData`; use it when filter changes should
  keep the previous table visible while the new key loads.
- `refetchOnWindowFocus: false` belongs on expensive reads even though the
  global default already disables focus refetches (`web/src/main.tsx:45-49`).

Initial loads still render their skeleton. Background refetches render the
existing data plus a small `isFetching && !isLoading` status hint
(`web/src/routes/projects/sourcing/ProjectSourcingPage.tsx:895-900`).

## QueryClient defaults

`web/src/main.tsx:45-49`:

```ts
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  queryCache: new QueryCache({ onError: on401 }),
  mutationCache: new MutationCache({ onError: on401 }),
});
```

- `retry: false` — list endpoints already return `{ items: [] }` on
  empty workspace, so a transient 5xx surfaces as `query.isError` once
  rather than three times.
- `refetchOnWindowFocus: false` — too aggressive for a stock-management
  app where ledger state is operator-driven.
- `onError: on401` — single 401 handler for both queries AND mutations.

## `useApiMutation`

`web/src/lib/mutations.ts:42-91`. Thin wrapper around `useMutation` that
locks `ApiError` as the error generic so callers narrow on it without
re-stating it (`mutations.ts:53-67`):

```ts
// web/src/lib/mutations.ts:87-91
export function useApiMutation<TOut = unknown, TIn = void>(
  options: ApiMutationOptions<TOut, TIn>,
): ApiMutationResult<TOut, TIn> {
  return useMutation<TOut, ApiError, TIn>(options);
}
```

Why a wrapper exists at all (`mutations.ts:1-41`):

- Pre-FE2-006, every form rolled its own `useState`-driven busy/err
  cycle. No `mutationKey` de-dup, so `OrderDetail.addEntry` was
  double-submittable on slow networks (server appended a duplicate row).
- `main.tsx` already wires `MutationCache` with the same `on401`
  handler as `QueryCache`, so once mutations flow through `useMutation`
  the auth-bus redirect path comes for free.

### Canonical call site

```tsx
// web/src/lib/mutations.ts:75-86 (example from the docstring)
const addEntry = useApiMutation({
  mutationKey: ["order", orderId, "add-entry"],
  mutationFn: (input: AddEntryRequest) =>
    api.post(`/orders/${orderId}/entries`, input),
  onSuccess: () =>
    qc.invalidateQueries({
      queryKey: wsKeyOf(workspaceId, "order", orderId),
    }),
});
<button disabled={addEntry.isPending} onClick={() => addEntry.mutate(...)}>
```

Two rules:

- `mutationKey` is the de-dup boundary. TanStack's mutation cache
  serialises mutations sharing a key, so a double-click can't fire two
  requests. Pick a key that names the **resource + action**, e.g.
  `["order", orderId, "add-entry"]`.
- The wrapper rethrows `ApiError`. Status-specific branches narrow on
  `e instanceof ApiError` — the 409 MPN-conflict flow in `PartCreate`
  reads `error.body.existing_id` / `existing_name` via
  `getConflictDetail` (see [api-layer](api-layer.md)).

The 401 → `authBus.emit("unauthorized")` redirect runs in
`MutationCache.onError`; the wrapper deliberately does NOT duplicate it
(`mutations.ts:30-33`).

### Pinning tests

`web/src/lib/__dom__/mutations.dom.test.tsx`:

- "dedupes concurrent submits sharing a mutationKey" (line 54) — the
  `OrderDetail.addEntry` regression.
- "button gated on isPending blocks the second click entirely" (line 111)
   — the operator-facing UI gate.
- "rethrows ApiError to the caller's onError" (line 158) — the 409 / 422
  branch contract.
- "a 401 from a mutation fires authBus 'unauthorized' exactly once" (line 191)
  — the redirect-path wiring.

## Auth bus

The bus lives in `queryKeys.ts:158-184` because both the QueryCache 401
handler and the workspace-scoped key helpers are pure-JS modules outside
the React tree. Keeping them together avoids a separate file and a
circular import:

```ts
// web/src/lib/queryKeys.ts:163-184
type AuthEvent = "unauthorized";
type Listener = (event: AuthEvent) => void;
const listeners = new Set<Listener>();
export const authBus = {
  emit(event: AuthEvent) { for (const fn of listeners) try { fn(event); } catch {} },
  on(fn: Listener): () => void { listeners.add(fn); return () => listeners.delete(fn); },
};
```

`AuthProvider` subscribes (`web/src/lib/auth.tsx:148-161`) and
translates `"unauthorized"` into a `nav("/login", { state: { from: location } })`.
`main.tsx:39-43` is the only emitter — see [auth-flow](auth-flow.md).

Listeners that throw don't break other listeners (`queryKeys.ts:170-175`)
— a single bad subscription can't wedge the redirect path.
