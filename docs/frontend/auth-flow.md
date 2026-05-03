# Auth flow

Audience: engineer

How `<AuthProvider>`, the workspace switcher, and the 401 redirect bus fit
together. Server-side session cookies are CLAUDE.md territory; this page is
about the FE state machine that surrounds them.

## `AuthProvider`

`web/src/lib/auth.tsx:30-168`. Mounts once at the top of the authed tree
(`web/src/App.tsx:171`). Owns four pieces of state:

```ts
// web/src/lib/auth.tsx:31-35
const [me, setMe] = useState<Me | null>(null);
const [loading, setLoading] = useState(true);
const [workspaceId, setWorkspaceId] = useState<string | null>(
  localStorage.getItem("workspaceId")
);
```

`workspaceId` is bootstrapped from localStorage so reloads remember the
last-active workspace. `me` is the parsed `/auth/me` response (see
[api-layer](api-layer.md)).

The context exposes (`auth.tsx:19-26`):

```ts
type Ctx = {
  me: Me | null;
  loading: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
  workspaceId: string | null;
  switchWorkspace: (id: string) => Promise<void>;
};
```

## Bootstrap effect

`auth.tsx:114-140`. Runs once on mount. The cleanup aborts the in-flight
`/auth/me` if the provider unmounts mid-request (v1 FE HIGH-4):

```tsx
// web/src/lib/auth.tsx:114-140
useEffect(() => {
  const ctrl = new AbortController();
  (async () => {
    try {
      const data = await api.parsed.get("/auth/me", MeSchema, { signal: ctrl.signal });
      setMe(data);
      Sentry.setUser({ id: data.user.id, email: data.user.email });
      if (!workspaceIdRef.current && data.workspaces[0]) {
        const first = data.workspaces[0].id;
        setWorkspaceId(first);
        localStorage.setItem("workspaceId", first);
      }
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) {
        if (!(e instanceof Error && e.name === "AbortError")) {
          Sentry.captureException(e);
        }
      }
      setMe(null);
      Sentry.setUser(null);
    } finally { setLoading(false); }
  })();
  return () => ctrl.abort();
}, []);
```

Three things to notice:

1. `api.parsed.get` is used — schema mismatch on `/auth/me` would cascade
   into every authed page, so this is the highest-stakes path to validate
   (`auth.tsx:53-55` for the comment).
2. 401 is the unauthenticated path and goes silent (no Sentry noise).
3. `AbortError` is also silenced — that's the unmount cleanup, not a
   real failure.

### `workspaceIdRef`

`auth.tsx:45-48`. The bootstrap effect's closure captures `workspaceId`
once at mount. If `refresh()` ever read it through that closure it would
see a stale value (v1 FE HIGH-1). Holding it in a ref means the latest
workspaceId is always reachable without recreating the effect.

## `refresh()`

`auth.tsx:50-80`. Same shape as the bootstrap, minus the AbortController
(callers run it after a known-OK action, e.g. accepting an invitation).
Reads `workspaceIdRef.current` for the same staleness reason
(`auth.tsx:60-67`).

## `logout()`

`auth.tsx:82-91`. POSTs `/auth/logout`, drops the workspaceId from
localStorage, clears `me` + `workspaceId` state, clears the Sentry user,
and calls `qc.clear()` to wipe the React-Query cache. Errors from the
POST are swallowed — the cookie may already be invalid, and the local
cleanup must still run.

## `switchWorkspace(id)`

`auth.tsx:93-110`:

```tsx
// web/src/lib/auth.tsx:93-110
const switchWorkspace = useCallback(async (id: string) => {
  await api.post(`/workspaces/${id}/switch`);
  localStorage.setItem("workspaceId", id);
  qc.clear();
  setWorkspaceId(id);
  nav("/parts", { replace: true });
}, [qc, nav]);
```

Order matters:

1. **Wait** for the server-side cookie session to flip before flushing
   the cache — otherwise the next refetch hits the new key with the old
   session.
2. Update localStorage so a reload still lands on the new workspace.
3. `qc.clear()` (FE2-003). The workspace-scoped key prefix
   (see [tanstack-patterns](tanstack-patterns.md)) means stale data
   couldn't bleed across, but freeing memory and forcing a clean refetch
   is the right move.
4. Update React state.
5. `nav("/parts", { replace: true })` instead of a full reload (FE2-002)
   — a full reload tore down WebSocket / scanner state for no benefit.
   `<AppShell>` survives the navigation because of the layout-route
   pattern (`web/src/App.tsx:118-131`, see [routing](routing.md)).

## 401 redirect bus

Three pieces:

### Emitter — `main.tsx`

`web/src/main.tsx:39-49`. The QueryClient's `QueryCache` and
`MutationCache` share a single `onError` handler:

```ts
// web/src/main.tsx:39-49
function on401(err: unknown) {
  if (err instanceof ApiError && err.status === 401) {
    authBus.emit("unauthorized");
  }
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  queryCache: new QueryCache({ onError: on401 }),
  mutationCache: new MutationCache({ onError: on401 }),
});
```

Pre-fix (FE2-001) every list page caught its own `ApiError(401)` and
rendered an empty list — the user couldn't tell whether their session
expired or the workspace really was empty.

The handler runs **outside React**, so `useNavigate()` isn't usable
here. The bus pattern keeps this dependency-free and avoids tight
coupling to the router (`main.tsx:25-38` for the comment).

### Bus — `queryKeys.ts`

`web/src/lib/queryKeys.ts:158-184`. Single event type, single
`Set<Listener>`, errors in one listener don't break others
(see [tanstack-patterns](tanstack-patterns.md) for the full surface):

```ts
// web/src/lib/queryKeys.ts:163-184
type AuthEvent = "unauthorized";
export const authBus = {
  emit(event: AuthEvent) { for (const fn of listeners) try { fn(event); } catch {} },
  on(fn: Listener): () => void { listeners.add(fn); return () => listeners.delete(fn); },
};
```

### Subscriber — `AuthProvider`

`web/src/lib/auth.tsx:148-161`:

```tsx
// web/src/lib/auth.tsx:148-161
useEffect(() => {
  return authBus.on((event) => {
    if (event !== "unauthorized") return;
    if (location.pathname === "/login" || location.pathname === "/signup") return;
    localStorage.removeItem("workspaceId");
    setMe(null);
    setWorkspaceId(null);
    Sentry.setUser(null);
    qc.clear();
    nav("/login", { replace: true, state: { from: location } });
  });
}, [nav, qc, location]);
```

Three reasons it lives in a React effect rather than the `QueryCache`
callback:

- It needs the router (`useNavigate`) to push `/login`.
- It needs `location` to populate `state.from` so the deep-link
  preservation works (FE2-001 + FE2-010).
- It needs to debounce: hitting `/login` itself with a stale 401 can't
  loop the redirect — the early return at line 153 is the loop-breaker.

Pinning test: `web/src/lib/__dom__/mutations.dom.test.tsx:191-220`
asserts a 401 from `useMutation` fires `authBus "unauthorized"` exactly
once.

## Deep-link preservation (FE2-010)

Two-way:

### Outbound — protected route → `/login`

`<Gate>` (`web/src/App.tsx:118-131`) and the auth-bus handler both stash
`location` on `state.from` before navigating to `/login`:

```tsx
// web/src/App.tsx:125
if (!me) return <Navigate to="/login" replace state={{ from: location }} />;

// web/src/lib/auth.tsx:159
nav("/login", { replace: true, state: { from: location } });
```

### Inbound — `/login` → original target

`<RedirectIfAuthed>` (`web/src/App.tsx:140-153`) reads `state.from` after
sign-in and bounces:

```tsx
// web/src/App.tsx:143-156
if (me) {
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
}
```

The `pathname !== "/login"` guard prevents a `/login → /login` loop
(FE2-019). Default landing is `/parts`. The redirect target carries
`pathname`, `search`, and `hash` so deep-links like
`/parts/scan-import?storage_id=abc&tab=queue` or
`/parts/abc?tab=specs#anchor` survive the auth round-trip (PR #311,
issue #304). See [routing](routing.md) for the route-tree side of the
same dance.

## Sentry user identity

`auth.tsx:59-60`, `:74-76`, `:88-89`, `:120-121`, `:133-134`. The user's
identity is pushed into Sentry on every `me` transition so events can be
deduped by user (FE MED-10). On logout / 401 / refresh failure it's set
to `null` so post-logout errors aren't attributed to the prior user.
