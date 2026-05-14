import { createContext, useContext } from "react";

import type { Me } from "./schemas";

export type AuthContextValue = {
  me: Me | null;
  loading: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
  workspaceId: string | null;
  switchWorkspace: (id: string) => Promise<void>;
};

export const AuthCtx = createContext<AuthContextValue | undefined>(undefined);

export function useOptionalAuth(): AuthContextValue | undefined {
  return useContext(AuthCtx);
}
