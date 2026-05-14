import {
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import * as Sentry from "@sentry/react";
import { api, ApiError } from "./api";
import { AuthCtx, useOptionalAuth, type AuthContextValue } from "./authContext";
import { authBus } from "./queryKeys";
import { MeSchema, type Me } from "./schemas";

export type { Me };
export { useOptionalAuth };

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [workspaceId, setWorkspaceId] = useState<string | null>(
    localStorage.getItem("workspaceId")
  );
  const qc = useQueryClient();
  const nav = useNavigate();
  const location = useLocation();

  // The bootstrap effect fires once and the closure it captures keeps
  // pointing at the *original* `workspaceId` forever — if we ever
  // referenced it from inside refresh() through a closure we'd see a
  // stale value (v1 FE HIGH-1). Holding it in a ref means the latest
  // workspaceId is always reachable without recreating the effect.
  const workspaceIdRef = useRef(workspaceId);
  useEffect(() => {
    workspaceIdRef.current = workspaceId;
  }, [workspaceId]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      // Parsed boundary: schema mismatch on /auth/me would cascade into
      // every authed page, so this is the highest-stakes path to validate.
      const data = await api.parsed.get("/auth/me", MeSchema);
      setMe(data);
      // Pipe the user's identity into Sentry so events can be deduped
      // by user (FE MED-10).
      Sentry.setUser({ id: data.user.id, email: data.user.email });
      // Read the latest workspaceId via ref — refresh() may be called
      // long after the provider mounted, and a stale closure here used
      // to clobber a freshly-switched workspace with the boot value.
      if (!workspaceIdRef.current && data.workspaces[0]) {
        const first = data.workspaces[0].id;
        setWorkspaceId(first);
        localStorage.setItem("workspaceId", first);
      }
    } catch (e) {
      // 401 is the unauthenticated path (no log noise). Anything else
      // — including ApiError(0, schema_mismatch) — goes to Sentry so
      // ops sees real shape drift rather than silent UI degradation.
      if (!(e instanceof ApiError && e.status === 401)) {
        Sentry.captureException(e);
      }
      setMe(null);
      Sentry.setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } catch {}
    localStorage.removeItem("workspaceId");
    setMe(null);
    setWorkspaceId(null);
    Sentry.setUser(null);
    qc.clear();
  }, [qc]);

  const switchWorkspace = useCallback(
    async (id: string) => {
      // Cookie session is updated server-side; await it before flushing
      // the cache so the next refetch reads the new workspace.
      await api.post(`/workspaces/${id}/switch`);
      localStorage.setItem("workspaceId", id);
      // Drop every cached query — the workspace-scoped key prefix means
      // stale data wouldn't bleed across, but freeing the memory and
      // forcing a clean refetch is the right move on switch (FE2-003).
      qc.clear();
      setWorkspaceId(id);
      // Land on the home view rather than reloading the page — a full
      // reload tore down WebSocket / scanner state for no benefit
      // (FE2-002).
      nav("/parts", { replace: true });
    },
    [qc, nav],
  );

  // Initial bootstrap — run once on mount. Cleanup aborts the in-flight
  // /auth/me if the provider unmounts mid-request (v1 FE HIGH-4).
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
          // Aborted fetches throw DOMException("AbortError") — ignore.
          if (!(e instanceof Error && e.name === "AbortError")) {
            Sentry.captureException(e);
          }
        }
        setMe(null);
        Sentry.setUser(null);
      } finally {
        setLoading(false);
      }
    })();
    return () => ctrl.abort();
  }, []);

  // Subscribe to the auth bus so a 401 thrown by *any* query or
  // mutation drops session state and bounces the user to /login. The
  // 401 handler in main.tsx fires the event; this effect translates it
  // into navigation. Keeping the redirect inside React (rather than the
  // QueryCache callback) means we still have access to the router and
  // can preserve the deep-link via location state (FE2-001 + FE2-010).
  useEffect(() => {
    return authBus.on((event) => {
      if (event !== "unauthorized") return;
      // Avoid stomping on the login/signup pages — there's nowhere to
      // bounce to and we'd loop the redirect.
      if (location.pathname === "/login" || location.pathname === "/signup") return;
      localStorage.removeItem("workspaceId");
      setMe(null);
      setWorkspaceId(null);
      Sentry.setUser(null);
      qc.clear();
      nav("/login", { replace: true, state: { from: location } });
    });
  }, [nav, qc, location]);

  return (
    <AuthCtx.Provider value={{ me, loading, refresh, logout, workspaceId, switchWorkspace }}>
      {children}
    </AuthCtx.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
