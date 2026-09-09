import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useOptionalAuth } from "@/lib/authContext";

/**
 * Remembered collapse state for a layout panel, per workspace.
 *
 * Two panels use this: the app sidebar (`AppShell`) and the parts-list
 * category rail. Both are chrome rather than content — a user who has
 * collapsed one wants it collapsed on the next visit too, and a
 * preference that resets on every reload is worse than no preference.
 *
 * **This deliberately mirrors `DataTable`'s persistence**, which is the
 * pattern the app already had for "a bit of view state the server should
 * never see": a `ws:<id>:…` localStorage key, a JSON blob, a read that
 * falls back to the default on anything unexpected, and a swap-on-
 * workspace-change effect that adopts the new workspace's saved value
 * rather than writing the old one over it. Keep the two in step; if you
 * change the key shape here, change `dataTableStorageKey` too.
 *
 * **Every access is wrapped.** Safari's private mode and a few
 * enterprise/privacy configurations throw on `localStorage` — on read
 * *and* on write, not just on write. A thrown preference must degrade to
 * "panel works, choice isn't remembered", never to a blank screen.
 *
 * The workspace id is read the same way `DataTable` reads it: from the
 * auth context when there is one, falling back to the `workspaceId` key
 * that `AuthProvider` itself writes. That fallback is what lets the hook
 * be used from a component rendered outside the provider in a test.
 */

type Persisted = { collapsed?: boolean };

/** The localStorage key a panel's collapse state is stored under. */
export function panelCollapseStorageKey(
  panelId: string,
  workspaceId: string | null | undefined,
): string {
  return `ws:${workspaceId ?? "none"}:panel:${panelId}`;
}

/**
 * The stored preference, or `undefined` when there isn't a usable one.
 *
 * `undefined` covers every failure the caller treats identically: no key,
 * an empty value, malformed JSON, a JSON value that isn't an object, and
 * a `collapsed` that isn't a boolean. All of them mean "fall back to the
 * default", which is expanded — never a state the user has to clear site
 * data to escape.
 */
function loadCollapsed(storageKey: string): boolean | undefined {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return undefined;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return undefined;
    const value = (parsed as Persisted).collapsed;
    return typeof value === "boolean" ? value : undefined;
  } catch {
    return undefined;
  }
}

function saveCollapsed(storageKey: string, collapsed: boolean): void {
  try {
    localStorage.setItem(storageKey, JSON.stringify({ collapsed } satisfies Persisted));
  } catch {
    // Storage disabled or over quota. The panel still toggles for this
    // session; only the memory of the choice is lost.
  }
}

export type PanelCollapse = {
  collapsed: boolean;
  toggle: () => void;
};

export function usePanelCollapse(
  panelId: string,
  defaultCollapsed = false,
): PanelCollapse {
  const auth = useOptionalAuth();
  const workspaceId = auth ? auth.workspaceId : storedWorkspaceId();
  const storageKey = useMemo(
    () => panelCollapseStorageKey(panelId, workspaceId),
    [panelId, workspaceId],
  );

  const [collapsed, setCollapsed] = useState<boolean>(
    () => loadCollapsed(storageKey) ?? defaultCollapsed,
  );
  const activeStorageKeyRef = useRef(storageKey);

  // Write-back. Guarded on the key the current state actually belongs to,
  // so the render between "workspace switched" and "state swapped" below
  // does not stamp the old workspace's choice onto the new one.
  useEffect(() => {
    if (activeStorageKeyRef.current !== storageKey) return;
    saveCollapsed(storageKey, collapsed);
  }, [storageKey, collapsed]);

  // Workspace switch: adopt the new key's saved value.
  useEffect(() => {
    if (activeStorageKeyRef.current === storageKey) return;
    activeStorageKeyRef.current = storageKey;
    setCollapsed(loadCollapsed(storageKey) ?? defaultCollapsed);
  }, [storageKey, defaultCollapsed]);

  const toggle = useCallback(() => setCollapsed((prev) => !prev), []);

  return { collapsed, toggle };
}

function storedWorkspaceId(): string | null {
  try {
    return localStorage.getItem("workspaceId");
  } catch {
    return null;
  }
}
