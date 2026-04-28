import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import AuthShell from "./AuthShell";

export default function Signup() {
  const nav = useNavigate();
  const { refresh } = useAuth();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await api.post("/auth/signup", {
        name,
        email,
        password,
        workspace_name: workspaceName || undefined,
      });
      await refresh();
      nav("/parts");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Signup failed");
    } finally {
      setBusy(false);
    }
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
