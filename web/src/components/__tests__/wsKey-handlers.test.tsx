/**
 * Pinning test for the Rules-of-Hooks fix on PR #33.
 *
 * Background: `wsKey()` (now `useWsKey()`) reads `useAuth()` and is
 * therefore a React hook. The original PR called it from event
 * handlers (`onClick`, mutation `onSuccess`, …), which run outside
 * React's dispatch window — every such call threw "Invalid hook call"
 * the moment the user touched the button. The whole mutation surface
 * (archive, move, add stock, receive order, etc.) crashed on click.
 *
 * The fix is to keep `useWsKey()` for render bodies and use the
 * vanilla `wsKeyOf(workspaceId, ...)` from event handlers, after
 * capturing `workspaceId` from `useAuth()` at render time.
 *
 * The repo has no `@testing-library/react` and no jsdom env (per
 * `web/src/components/DataTable.test.tsx`'s preamble). Adding either
 * is out of scope per the PR's constraint of no new deps. Instead
 * this pin asserts the contract directly:
 *
 *   1. `wsKeyOf(...)` is a vanilla function — calling it outside any
 *      React render does NOT throw, and the shape matches the prefix
 *      every cache lookup expects.
 *   2. `useWsKey(...)` IS a hook — calling it bare from a handler-
 *      style callback throws (which is exactly the regression PR #33
 *      shipped on the first pass). The fact that this throws is *the
 *      reason* every mutation was crashing.
 *   3. A handler closure that captures `workspaceId` and builds keys
 *      via `wsKeyOf` works without any React dispatcher in scope —
 *      this models the post-fix call site (`qc.invalidateQueries({
 *      queryKey: wsKeyOf(workspaceId, "parts") })`).
 *
 * Together these cover the "no Invalid hook call" assertion the PR
 * comment asked for, without dragging in jsdom + RTL just to render
 * a click target.
 */
import { describe, expect, it } from "vitest";
import { useWsKey, wsKeyOf, wsScope } from "@/lib/queryKeys";

describe("wsKeyOf — vanilla helper is safe in event handlers", () => {
  it("returns the workspace-prefixed key without touching React", () => {
    expect(wsKeyOf("ws-1", "parts")).toEqual(["ws", "ws-1", "parts"]);
    expect(wsKeyOf("ws-1", "part", "abc", "stock")).toEqual([
      "ws",
      "ws-1",
      "part",
      "abc",
      "stock",
    ]);
  });

  it("collapses null / undefined workspaceId to 'none' so pre-bootstrap keys don't collide", () => {
    expect(wsKeyOf(null, "parts")).toEqual(["ws", "none", "parts"]);
    expect(wsKeyOf(undefined, "parts")).toEqual(["ws", "none", "parts"]);
  });

  it("wsScope is the workspace-only prefix used for blanket invalidation", () => {
    expect(wsScope("ws-1")).toEqual(["ws", "ws-1"]);
    expect(wsScope(null)).toEqual(["ws", "none"]);
  });
});

describe("useWsKey — hook nature", () => {
  it("throws when called outside a React render (proves it must NOT be in handlers)", () => {
    // This is exactly the failure mode PR #33 shipped on the first
    // pass: every `onClick={() => qc.invalidateQueries({ queryKey:
    // wsKey(...) })}` would land here. React's dispatcher is null
    // outside render, so `useContext(AuthCtx)` blows up with the
    // "Invalid hook call" / "null is not an object" message.
    expect(() => useWsKey("parts")).toThrow();
  });
});

describe("handler closure — the post-fix call shape", () => {
  it("captures workspaceId at render and uses wsKeyOf inside the handler without throwing", () => {
    // Model what every mutation site now does: pull workspaceId from
    // useAuth() at render time, then close over it from the handler.
    const workspaceId = "ws-42";

    function onArchiveClick() {
      // No hook call here — wsKeyOf is a vanilla function. This is
      // what the cache invalidation in PartsList / OrderDetail /
      // BuildDetail / Workspace / etc. now does.
      return wsKeyOf(workspaceId, "parts");
    }

    // Calling the handler outside of any React render must NOT throw
    // — that's the whole point of the rename + sweep.
    expect(() => onArchiveClick()).not.toThrow();
    expect(onArchiveClick()).toEqual(["ws", "ws-42", "parts"]);
  });

  it("nested handlers (e.g. mutation onSuccess) still work because the closure is plain JS", () => {
    const workspaceId = "ws-7";
    let invalidatedKey: unknown[] | null = null;
    function fakeMutation(onSuccess: () => void) {
      onSuccess();
    }
    fakeMutation(() => {
      invalidatedKey = wsKeyOf(workspaceId, "order", "abc");
    });
    expect(invalidatedKey).toEqual(["ws", "ws-7", "order", "abc"]);
  });
});
