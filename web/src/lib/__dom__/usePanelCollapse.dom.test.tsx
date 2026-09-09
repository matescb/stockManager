/**
 * `usePanelCollapse` — the persistence behind both collapsible panels.
 *
 * What is worth pinning here is the failure behaviour, not the happy
 * path. A remembered preference that can get stuck collapsed is a bug
 * the user cannot escape without devtools, so every way the stored value
 * can be unusable — absent, empty, malformed, wrong type, storage
 * throwing outright — has to land on "expanded".
 */
import type { ReactNode } from "react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";

import { AuthCtx, type AuthContextValue } from "@/lib/authContext";
import {
  panelCollapseStorageKey,
  usePanelCollapse,
} from "@/lib/usePanelCollapse";

const PANEL = "sidebar";

/** The key the hook uses with no auth context and no stored workspace. */
function keyForNoWorkspace(): string {
  return panelCollapseStorageKey(PANEL, null);
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("usePanelCollapse", () => {
  it("defaults to expanded when nothing is stored", () => {
    const { result } = renderHook(() => usePanelCollapse(PANEL));

    expect(result.current.collapsed).toBe(false);
  });

  it("honours an explicit default when nothing is stored", () => {
    const { result } = renderHook(() => usePanelCollapse(PANEL, true));

    expect(result.current.collapsed).toBe(true);
  });

  it("toggling writes the choice under a workspace-scoped key", () => {
    const { result } = renderHook(() => usePanelCollapse(PANEL));

    act(() => result.current.toggle());

    expect(result.current.collapsed).toBe(true);
    expect(localStorage.getItem(keyForNoWorkspace())).toBe(
      JSON.stringify({ collapsed: true }),
    );
  });

  it("the collapsed choice survives a remount", () => {
    const first = renderHook(() => usePanelCollapse(PANEL));
    act(() => first.result.current.toggle());
    first.unmount();

    const second = renderHook(() => usePanelCollapse(PANEL));

    expect(second.result.current.collapsed).toBe(true);
  });

  it("expanding again is remembered too", () => {
    const first = renderHook(() => usePanelCollapse(PANEL));
    act(() => first.result.current.toggle());
    act(() => first.result.current.toggle());
    first.unmount();

    const second = renderHook(() => usePanelCollapse(PANEL));

    expect(second.result.current.collapsed).toBe(false);
  });

  it.each([
    ["malformed JSON", "{not json"],
    ["an empty string", ""],
    ["a JSON primitive", '"collapsed"'],
    ["null", "null"],
    ["the wrong value type", '{"collapsed":"yes"}'],
    ["an unrelated shape", '{"hidden":{"mpn":true}}'],
  ])("falls back to expanded for %s", (_label, raw) => {
    localStorage.setItem(keyForNoWorkspace(), raw);

    const { result } = renderHook(() => usePanelCollapse(PANEL));

    expect(result.current.collapsed).toBe(false);
  });

  it("still toggles when localStorage throws on read and write", () => {
    // Safari's private mode and a few locked-down enterprise profiles
    // throw here, on both sides. The panel has to keep working.
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });

    const { result } = renderHook(() => usePanelCollapse(PANEL));
    expect(result.current.collapsed).toBe(false);

    act(() => result.current.toggle());
    expect(result.current.collapsed).toBe(true);
  });

  it("keeps separate state per workspace", () => {
    const other = panelCollapseStorageKey(PANEL, "ws-2");
    localStorage.setItem(other, JSON.stringify({ collapsed: true }));

    // No auth context, so the hook reads the workspace id AuthProvider
    // stores — the same fallback DataTable uses.
    localStorage.setItem("workspaceId", "ws-2");
    const inWs2 = renderHook(() => usePanelCollapse(PANEL));
    expect(inWs2.result.current.collapsed).toBe(true);
    inWs2.unmount();

    localStorage.setItem("workspaceId", "ws-1");
    const inWs1 = renderHook(() => usePanelCollapse(PANEL));
    expect(inWs1.result.current.collapsed).toBe(false);
    // ws-2's choice was not overwritten by ws-1's render.
    expect(localStorage.getItem(other)).toBe(JSON.stringify({ collapsed: true }));
  });

  /**
   * The two effects in the hook are ordered, and the write-back's guard is
   * load-bearing rather than defensive: without it, the render right after
   * a workspace switch would stamp the *old* workspace's value onto the
   * *new* key, and the swap effect would then read back what it had just
   * destroyed. Only a live switch — same mount, changing workspace id —
   * exercises that ordering, so it gets its own test.
   */
  it("adopts the new workspace's value on a live switch, without clobbering either", () => {
    const ws1Key = panelCollapseStorageKey(PANEL, "ws-1");
    const ws2Key = panelCollapseStorageKey(PANEL, "ws-2");
    localStorage.setItem(ws2Key, JSON.stringify({ collapsed: true }));

    // `renderHook`'s `rerender` cannot swap the wrapper, so the wrapper
    // reads a mutable id — which is what a real workspace switch looks
    // like from the hook's side: the same mount, a new context value.
    let workspaceId = "ws-1";
    function Wrapper({ children }: { children: ReactNode }) {
      return (
        <AuthCtx.Provider value={{ workspaceId } as AuthContextValue}>
          {children}
        </AuthCtx.Provider>
      );
    }

    const { result, rerender } = renderHook(() => usePanelCollapse(PANEL), {
      wrapper: Wrapper,
    });
    expect(result.current.collapsed).toBe(false);

    workspaceId = "ws-2";
    rerender();

    // Drop the guard and this reads `false`: the write-back would put ws-1's
    // state under ws-2's key first, and the swap would read that back.
    expect(result.current.collapsed).toBe(true);

    act(() => result.current.toggle());
    expect(localStorage.getItem(ws2Key)).toBe(JSON.stringify({ collapsed: false }));
    // ws-1's choice survived the switch untouched.
    expect(localStorage.getItem(ws1Key)).toBe(JSON.stringify({ collapsed: false }));
  });

  it("panels with different ids do not share state", () => {
    const sidebar = renderHook(() => usePanelCollapse("sidebar"));
    act(() => sidebar.result.current.toggle());

    const rail = renderHook(() => usePanelCollapse("parts-categories"));

    expect(sidebar.result.current.collapsed).toBe(true);
    expect(rail.result.current.collapsed).toBe(false);
  });
});
