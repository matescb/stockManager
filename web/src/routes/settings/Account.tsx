import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function Account() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function accept() {
    if (!token.trim()) return;
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const out = await api.post<{ workspace_id: string; workspace_name: string; role: string }>(
        "/invitations/accept",
        { token: token.trim() },
      );
      setMsg(`Joined "${out.workspace_name}" as ${out.role}.`);
      setToken("");
      qc.invalidateQueries();
      // Switch to the joined workspace
      await api.post(`/workspaces/${out.workspace_id}/switch`);
      window.location.reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  if (!me) return null;
  return (
    <div className="max-w-xl space-y-4">
      <h1 className="text-xl font-semibold">Account</h1>
      <div className="card p-4 space-y-3 text-sm">
        <div><span className="text-muted">Name:</span> {me.user.name}</div>
        <div><span className="text-muted">Email:</span> {me.user.email}</div>
        <div><span className="text-muted">User ID:</span> <span className="font-mono">{me.user.id}</span></div>
      </div>
      <div className="card p-4 space-y-2">
        <h2 className="text-md font-semibold">Accept workspace invitation</h2>
        <p className="text-sm text-muted">
          Paste an invitation token here to join a workspace someone invited you to.
          The invitation must have been issued for <code>{me.user.email}</code>.
        </p>
        {err && <div className="text-danger text-sm">{err}</div>}
        {msg && <div className="text-success text-sm">{msg}</div>}
        <div className="flex gap-2">
          <input className="input flex-1" value={token} onChange={e => setToken(e.target.value)} placeholder="paste token" />
          <button className="btn-primary" onClick={accept} disabled={busy}>{busy ? "…" : "Accept"}</button>
        </div>
      </div>
    </div>
  );
}
