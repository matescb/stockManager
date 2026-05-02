import { useState } from "react";
import { Link, useLocation, useNavigate, type Location } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import AuthShell from "./AuthShell";

export default function Login() {
  const nav = useNavigate();
  const location = useLocation();
  const { refresh } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  // SEC2-014: the server returns retry_after_seconds on a 429 lockout
  // response. Show a countdown so the user knows how long to wait.
  const [retryAfter, setRetryAfter] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  // `from` is set by `<Gate>` (and by the 401 handler in auth.tsx) when
  // an authed page bounced the user here. Replay the original target on
  // success rather than always landing them on /parts (FE2-010).
  const fromLoc = (location.state as { from?: Location } | null)?.from;
  const fromPath = fromLoc ? `${fromLoc.pathname}${fromLoc.search}${fromLoc.hash}` : null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setRetryAfter(null);
    setBusy(true);
    try {
      await api.post("/auth/login", { email, password });
      await refresh();
      nav(fromPath || "/parts", { replace: true });
    } catch (e) {
      if (e instanceof ApiError) {
        // SEC2-014: per-account lockout returns 429 with retry_after_seconds.
        const body = e.body as Record<string, unknown> | null;
        if (e.status === 429 && body && typeof body.retry_after_seconds === "number") {
          setRetryAfter(body.retry_after_seconds);
          setErr("Too many failed login attempts. Please try again later.");
        } else {
          setErr(e.message);
        }
      } else {
        setErr("Login failed");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Sign in">
      <form onSubmit={submit} className="space-y-4">
        {err && (
          <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-danger text-sm">
            {err}
            {retryAfter !== null && (
              <span className="block mt-1 text-xs">
                Try again in {Math.ceil(retryAfter / 60)} minute{retryAfter > 60 ? "s" : ""}.
              </span>
            )}
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
