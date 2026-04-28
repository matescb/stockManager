import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import { api, ApiError } from "./api";

export type Me = {
  user: { id: string; email: string; name: string };
  workspaces: { id: string; name: string; kind: string }[];
};

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
      const data = await api.get<Me>("/auth/me");
      setMe(data);
      if (!workspaceId && data.workspaces[0]) {
        setWorkspaceId(data.workspaces[0].id);
        localStorage.setItem("workspaceId", data.workspaces[0].id);
      }
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) console.error(e);
      setMe(null);
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
