import { useState } from "react";
import { Link, useLocation, useNavigate, type Location } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApiMutation } from "@/lib/mutations";
import AuthShell from "./AuthShell";

export default function Login() {
  const nav = useNavigate();
  const location = useLocation();
  const { refresh } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);

  // `from` is set by `<Gate>` (and by the 401 handler in auth.tsx) when
  // an authed page bounced the user here. Replay the original target on
  // success rather than always landing them on /parts (FE2-010).
  const fromLoc = (location.state as { from?: Location } | null)?.from;
  const fromPath = fromLoc ? `${fromLoc.pathname}${fromLoc.search}${fromLoc.hash}` : null;

  const loginMutation = useApiMutation<unknown, { email: string; password: string }>({
    mutationKey: ["auth", "login"],
    mutationFn: ({ email, password }) => api.post("/auth/login", { email, password }),
    onSuccess: async () => {
      await refresh();
      nav(fromPath || "/parts", { replace: true });
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        setErr("Login failed. Check your credentials and try again.");
      } else {
        setErr("Login failed");
      }
    },
  });

  const busy = loginMutation.isPending;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    loginMutation.mutate({ email, password });
  }

  return (
    <AuthShell title="Sign in">
      <form onSubmit={submit} className="space-y-4">
        {err && (
          <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-danger text-sm">
            {err}
          </div>
        )}
        <div>
          <label className="label" htmlFor="login-email">Email</label>
          <input
            id="login-email"
            className="input"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={e => setEmail(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="login-password">Password</label>
          <input
            id="login-password"
            className="input"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={e => setPassword(e.target.value)}
          />
        </div>
        <button className="btn-primary w-full" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="text-sm text-muted">
          No account? <Link to="/signup" className="text-accent hover:underline">Sign up</Link>
        </p>
      </form>
    </AuthShell>
  );
}
