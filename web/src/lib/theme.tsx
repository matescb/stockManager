import { createContext, ReactNode, useContext, useEffect, useState } from "react";

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

type Ctx = {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (p: ThemePreference) => void;
};

const STORAGE_KEY = "theme";

const ThemeCtx = createContext<Ctx | undefined>(undefined);

function readPref(): ThemePreference {
  const v = localStorage.getItem(STORAGE_KEY);
  return v === "light" || v === "dark" ? v : "system";
}

function systemPrefersDark(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function resolve(pref: ThemePreference): ResolvedTheme {
  if (pref === "system") return systemPrefersDark() ? "dark" : "light";
  return pref;
}

function apply(theme: ResolvedTheme) {
  const root = document.documentElement;
  if (theme === "dark") root.classList.add("dark");
  else root.classList.remove("dark");
}

// Apply theme class as early as possible to avoid a flash. Called from main.tsx
// before ReactDOM renders.
export function bootTheme(): ResolvedTheme {
  const pref = readPref();
  const r = resolve(pref);
  apply(r);
  return r;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readPref);
  const [resolved, setResolved] = useState<ResolvedTheme>(() => resolve(preference));

  // Re-resolve when preference changes.
  useEffect(() => {
    const r = resolve(preference);
    setResolved(r);
    apply(r);
  }, [preference]);

  // Track system preference if the user picked "system".
  useEffect(() => {
    if (preference !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      const r: ResolvedTheme = mq.matches ? "dark" : "light";
      setResolved(r);
      apply(r);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [preference]);

  function setPreference(p: ThemePreference) {
    if (p === "system") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, p);
    setPreferenceState(p);
  }

  return (
    <ThemeCtx.Provider value={{ preference, resolved, setPreference }}>
      {children}
    </ThemeCtx.Provider>
  );
}

export function useTheme(): Ctx {
  const ctx = useContext(ThemeCtx);
  if (!ctx) throw new Error("useTheme must be used inside <ThemeProvider>");
  return ctx;
}
