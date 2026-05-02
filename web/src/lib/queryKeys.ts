/**
 * Workspace-scoped TanStack Query keys.
 *
 * Why this exists: every list query is implicitly tenant-bound (the
 * backend filters by the cookie's session workspace), but the cache key
 * was just `["parts"]`, `["orders"]`, … — so when the user switched
 * workspace, the in-memory cache from the *previous* workspace served
 * the next render before refetch landed. That's a multi-tenant data
 * leak waiting to happen (FE2-004 / v1 FE HIGH-8).
 *
 * The fix is twofold:
 *
 *  1. Every query key is now prefixed with `["ws", <workspaceId>, …]`,
 *     so TanStack treats two different workspaces as distinct caches.
 *     Callers build keys inside render with `useWsKey("parts", ...)`
 *     instead of `["parts", ...]`. Outside render (event handlers,
 *     mutation `onSuccess`, etc.) use `wsKeyOf(workspaceId, ...)` —
 *     `useWsKey` is a hook (it reads `useAuth()`), so calling it from
 *     a click handler crashes with "Invalid hook call".
 *
 *  2. On workspace switch we still call `qc.clear()` to free the old
 *     cache (no point keeping it around), but the prefix means even
 *     without that step a refetch never reads the wrong tenant's data.
 *
 * The auth bus piggybacks here because the 401 handler at QueryClient
 * construction time has no React context — it needs a vanilla pub/sub
 * to nudge `<AuthProvider>` to drop session state and `<App>` to redirect.
 */
import { useAuth } from "./auth";

// ---------------------------------------------------------------------
// Workspace-keyed query helpers
// ---------------------------------------------------------------------

/**
 * Build a query key scoped to the active workspace. Reads `workspaceId`
 * from `useAuth()` so it MUST be called inside a function component
 * render body (it's a hook). The prefix is `["ws", <id-or-"none">]`;
 * null / undefined collapse to "none" so SSR or pre-bootstrap renders
 * don't collide with real workspace caches. The `use` prefix makes the
 * hook nature loud — for event handlers and mutation callbacks use
 * `wsKeyOf(workspaceId, ...)` instead.
 */
export function useWsKey(...rest: unknown[]): unknown[] {
  const { workspaceId } = useAuth();
  return ["ws", workspaceId ?? "none", ...rest];
}

/**
 * Vanilla (non-hook) variant for places that already hold the
 * workspaceId in scope and just want to build a key. Mostly used by
 * `qc.invalidateQueries(...)` after a mutation if the caller already
 * has the auth context.
 */
export function wsKeyOf(workspaceId: string | null | undefined, ...rest: unknown[]): unknown[] {
  return ["ws", workspaceId ?? "none", ...rest];
}

/** Top-level prefix for blanket invalidation of the active workspace. */
export function wsScope(workspaceId: string | null | undefined): unknown[] {
  return ["ws", workspaceId ?? "none"];
}

// ---------------------------------------------------------------------
// Narrow invalidation helpers — each returns a list of query keys
// (unknown[][]) so callers can loop:
//
//   for (const k of archivePartKeys(workspaceId, partId))
//     qc.invalidateQueries({ queryKey: k });
//
// All keys keep the ["ws", workspaceId, …] prefix enforced by wsKeyOf.
// ---------------------------------------------------------------------

/**
 * Keys to invalidate after archiving or restoring a part.
 * Covers the part list, the individual part cache, and the two
 * reports that roll up stock totals (low-stock, stock-value).
 */
export function archivePartKeys(
  workspaceId: string | null | undefined,
  partId: string,
): unknown[][] {
  return [
    wsKeyOf(workspaceId, "parts"),
    wsKeyOf(workspaceId, "part", partId),
    wsKeyOf(workspaceId, "report", "low-stock"),
    wsKeyOf(workspaceId, "report", "stock-value"),
  ];
}

/**
 * Keys to invalidate after archiving or restoring a storage location.
 * Covers the storage list, the individual location cache, and the
 * stock-value report.
 */
export function archiveStorageKeys(
  workspaceId: string | null | undefined,
  storageId: string,
): unknown[][] {
  return [
    wsKeyOf(workspaceId, "storage"),
    wsKeyOf(workspaceId, "storage", storageId),
    wsKeyOf(workspaceId, "report", "stock-value"),
  ];
}

/**
 * Keys to invalidate after a lot move or adjust-count.
 *
 * Both ops change the part's total on-hand quantity, which means the
 * parts list (drives `on_hand` column) and the three stock-rollup
 * reports (low-stock, stock-value, expiring) all need to refresh.
 * Same rationale as `archivePartKeys` — keep these in sync if you
 * touch one.
 *
 * @param lot         The lot object (must have at least `id` and `part_id`).
 * @param storageIds  Storage location IDs touched by the operation.
 *                    For a move, callers must include BOTH source and
 *                    destination — the source's "what's in this bin"
 *                    and history views go stale otherwise.
 */
export function lotMutationKeys(
  workspaceId: string | null | undefined,
  lot: { id: string; part_id: string },
  storageIds: string[] = [],
): unknown[][] {
  const keys: unknown[][] = [
    wsKeyOf(workspaceId, "parts"),
    wsKeyOf(workspaceId, "lots"),
    wsKeyOf(workspaceId, "lot", lot.id),
    // ["ws", ws, "part", partId] is a prefix match — TanStack invalidates
    // every sub-key (`:stock`, `:lots`, `:history`, `:custom-fields`, …)
    // off it, so don't enumerate them separately.
    wsKeyOf(workspaceId, "part", lot.part_id),
    wsKeyOf(workspaceId, "report", "low-stock"),
    wsKeyOf(workspaceId, "report", "stock-value"),
    wsKeyOf(workspaceId, "report", "expiring"),
  ];
  for (const sid of storageIds) {
    keys.push(wsKeyOf(workspaceId, "storage", sid));
  }
  return keys;
}

/**
 * Keys to invalidate after archiving or restoring a project.
 */
export function archiveProjectKeys(
  workspaceId: string | null | undefined,
  projectId: string,
): unknown[][] {
  return [
    wsKeyOf(workspaceId, "projects"),
    wsKeyOf(workspaceId, "project", projectId),
  ];
}

// ---------------------------------------------------------------------
// Auth bus — pub/sub for cross-cutting auth events fired from places
// that don't sit inside the React tree (e.g. QueryCache.onError).
// ---------------------------------------------------------------------

type AuthEvent = "unauthorized";
type Listener = (event: AuthEvent) => void;

const listeners = new Set<Listener>();

export const authBus = {
  emit(event: AuthEvent) {
    for (const fn of listeners) {
      try {
        fn(event);
      } catch {
        // Listener throws shouldn't break other listeners.
      }
    }
  },
  on(fn: Listener): () => void {
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  },
};
