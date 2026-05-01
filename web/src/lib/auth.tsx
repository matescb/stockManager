import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import * as Sentry from "@sentry/react";
import { api, ApiError } from "./api";
import { MeSchema, type Me } from "./schemas";

export type { Me };

type Ctx = {
  me: Me | null;
  loading: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
  workspaceId: string | null;
  switchWorkspace: (id: string) => Promise<void>;
};

const AuthCtx = createContext<Ctx>({} as any);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [workspaceId, setWorkspaceId] = useState<string | null>(
    localStorage.getItem("workspaceId")
  );

  async function refresh() {
    setLoading(true);
    try {
      // Parsed boundary: schema mismatch on /auth/me would cascade into
      // every authed page, so this is the highest-stakes path to validate.
      const data = await api.parsed.get("/auth/me", MeSchema);
      setMe(data);
      // Pipe the user's identity into Sentry so events can be deduped
      // by user (FE MED-10). We send the id + email; the backend's
      // before_send scrubber strips request bodies and tenant headers,
      // and Sentry's "send default PII" already handles IP, so this is
      // additive identification, not new exfiltration.
      Sentry.setUser({ id: data.user.id, email: data.user.email });
      if (!workspaceId && data.workspaces[0]) {
        setWorkspaceId(data.workspaces[0].id);
        localStorage.setItem("workspaceId", data.workspaces[0].id);
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
  }

  async function logout() {
    try {
      await api.post("/auth/logout");
    } catch {}
    localStorage.removeItem("workspaceId");
    setMe(null);
    setWorkspaceId(null);
    Sentry.setUser(null);
  }

  async function switchWorkspace(id: string) {
    await api.post(`/workspaces/${id}/switch`);
    localStorage.setItem("workspaceId", id);
    setWorkspaceId(id);
    window.location.reload();
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AuthCtx.Provider value={{ me, loading, refresh, logout, workspaceId, switchWorkspace }}>
      {children}
    </AuthCtx.Provider>
  );
}

export function useAuth() {
  return useContext(AuthCtx);
}
