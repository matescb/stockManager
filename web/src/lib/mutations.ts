/**
 * `useApiMutation` — thin wrapper around TanStack's `useMutation`.
 *
 * Why this exists (FE2-006):
 *  - Every form in `routes/**` was rolling its own `useState`-driven
 *    `busy` / `err` / `try-finally` cycle. There was no `mutationKey`
 *    de-dup, no rollback, no centralised place to attach a 422 parser,
 *    and at least `OrderDetail.addEntry` was double-submittable on slow
 *    networks (server appended a duplicate row).
 *  - `main.tsx` already wires a `MutationCache` with the same `on401`
 *    handler as `QueryCache`, so once mutations flow through
 *    `useMutation` the auth-bus redirect path comes for free — but the
 *    repo had zero `useMutation` call-sites (grep confirmed).
 *
 * Design notes:
 *  - `mutationFn` is the only runtime contract — pass an async function
 *    that calls `api.post/patch/delete/upload/parsed.*` (or anything
 *    that resolves to `TOut`). We do **not** add a parallel HTTP path;
 *    the wrapper exists purely so callers don't repeat the busy/err
 *    plumbing.
 *  - `mutationKey` names the mutation in the cache — it is what
 *    `useMutationState` and `isMutating` filter on. Pick one that names
 *    the *resource + action*, e.g. `["order", orderId, "add-entry"]`.
 *    It does **not** serialise anything: in TanStack v5 two mutations
 *    sharing a key still run concurrently. What serialises them is
 *    `scope: { id }`, and what stops a double-click is `disabled={…
 *    .isPending}` on the control. This comment used to claim otherwise,
 *    and a caller that believed it shipped a toggle that could race
 *    itself.
 *  - The wrapper rethrows the original `ApiError`. Callers narrow on
 *    `e instanceof ApiError` for status-specific branches (the 409
 *    MPN-conflict flow in `PartCreate` keeps reading `error.body` for
 *    `existing_id` / `existing_name`).
 *  - The 401 → `authBus.emit("unauthorized")` redirect runs in
 *    `MutationCache.onError` (see `main.tsx`); we deliberately do NOT
 *    duplicate it here.
 *
 * Cache invalidation:
 *  - Use `wsKeyOf(workspaceId, ...)` from `onSuccess` callbacks. Do not
 *    call `useWsKey` inside a callback — it's a hook and crashes
 *    outside render. CLAUDE.md ("frontend conventions") covers the
 *    invariant: every invalidation key must keep the
 *    `["ws", workspaceId, ...]` prefix or workspace isolation
 *    (FE2-004) silently regresses.
 */
import {
  useMutation,
  type UseMutationOptions,
  type UseMutationResult,
} from "@tanstack/react-query";
import { ApiError } from "./api";

/**
 * Same option surface as TanStack's `useMutation`, with `ApiError` as
 * the default error type so callers narrow on it without re-stating
 * the generic. The wrapper doesn't override any defaults — it only
 * exists to (a) lock the error generic and (b) give us a single seam
 * to add cross-cutting behaviour later (e.g. a 422 field-error parser
 * in a follow-up PR; out of scope for FE2-006).
 */
export type ApiMutationOptions<TOut, TIn> = UseMutationOptions<
  TOut,
  ApiError,
  TIn
>;

export type ApiMutationResult<TOut, TIn> = UseMutationResult<
  TOut,
  ApiError,
  TIn
>;

/**
 * The default mutation hook for forms. Pass at minimum a `mutationFn`
 * that calls one of the `api.*` helpers. Where a concurrent submit would
 * be a real bug — any write path that creates or appends a row — gate
 * the control on `isPending` and add `scope: { id }`; `mutationKey`
 * alone will not stop it.
 *
 * When the save is not finished until some query has refetched, RETURN
 * the invalidation promise from `onSuccess`. TanStack awaits it before
 * settling the mutation, so `isPending` covers the refetch too. See
 * `routes/parts/specColumnsSection.ts` for the case that needed it.
 *
 * Example:
 *   const addEntry = useApiMutation({
 *     mutationKey: ["order", orderId, "add-entry"],
 *     scope: { id: `order-add-entry:${orderId}` },
 *     mutationFn: (input: AddEntryRequest) =>
 *       api.post(`/orders/${orderId}/entries`, input),
 *     onSuccess: () =>
 *       qc.invalidateQueries({
 *         queryKey: wsKeyOf(workspaceId, "order", orderId),
 *       }),
 *   });
 *   <button disabled={addEntry.isPending} onClick={() => addEntry.mutate(...)}>
 */
export function useApiMutation<TOut = unknown, TIn = void>(
  options: ApiMutationOptions<TOut, TIn>,
): ApiMutationResult<TOut, TIn> {
  return useMutation<TOut, ApiError, TIn>(options);
}
