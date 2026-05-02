import { useState } from "react";
import { Link, useLocation, useNavigate, type Location } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import AuthShell from "./AuthShell";

export default function Signup() {
  const nav = useNavigate();
  const location = useLocation();
  const { refresh } = useAuth();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // SEC2-014: when the server enables email-verification the signup
  // returns 202 with status="verification_sent" instead of 200 with
  // a session cookie. We show a "check your inbox" view in that case.
  const [verificationSent, setVerificationSent] = useState(false);

  // Mirror the Login deep-link replay so a user who hit a protected
  // route, signed up instead of in, still lands on the original target.
  const fromLoc = (location.state as { from?: Location } | null)?.from;
  const fromPath = fromLoc ? `${fromLoc.pathname}${fromLoc.search}${fromLoc.hash}` : null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const data = await api.post("/auth/signup", {
        name,
        email,
        password,
        workspace_name: workspaceName || undefined,
      });
      // SEC2-014: email-verification path returns {status: "verification_sent"}.
      if ((data as { status?: string }).status === "verification_sent") {
        setVerificationSent(true);
        return;
      }
      // Legacy / dev path: immediate session — refresh and navigate.
      await refresh();
      nav(fromPath || "/parts", { replace: true });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Signup failed");
    } finally {
      setBusy(false);
    }
  }

  // "Check your inbox" view shown after a 202 verification_sent response.
  if (verificationSent) {
    return (
      <AuthShell title="Check your inbox">
        <div className="space-y-4">
          <p className="text-sm">
            We've sent a verification link to <strong>{email}</strong>.
            Click the link in the email to complete your account setup.
          </p>
          <p className="text-sm text-muted">
            Didn't receive it? Check your spam folder, or{" "}
            <button
              type="button"
              className="text-accent hover:underline"
              onClick={() => setVerificationSent(false)}
            >
              try again
            </button>
            .
          </p>
          <p className="text-sm text-muted">
            Already verified?{" "}
            <Link to="/login" className="text-accent hover:underline">Sign in</Link>
          </p>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Create account">
      <form onSubmit={submit} className="space-y-4">
        {err && (
          <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-danger text-sm">
            {err}
          </div>
        )}
        <div>
          <label className="label">Name</label>
          <input
            className="input"
            required
            value={name}
            onChange={e => setName(e.target.value)}
            autoComplete="name"
          />
        </div>
        <div>
          <label className="label">Email</label>
          <input
            className="input"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
          />
        </div>
        <div>
          <label className="label">Password (min 8)</label>
          <input
            className="input"
            type="password"
            required
            minLength={8}
            autoComplete="new-password"
            value={password}
            onChange={e => setPassword(e.target.value)}
          />
        </div>
        <div>
          <label className="label">Workspace name (optional)</label>
          <input
            className="input"
            value={workspaceName}
            onChange={e => setWorkspaceName(e.target.value)}
            placeholder="My Workspace"
          />
        </div>
        <button className="btn-primary w-full" disabled={busy}>
          {busy ? "Creating…" : "Create account"}
        </button>
        <p className="text-sm text-muted">
          Already have an account?{" "}
          <Link to="/login" className="text-accent hover:underline">Sign in</Link>
        </p>
      </form>
    </AuthShell>
  );
}
