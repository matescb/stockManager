import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type Ws = {
  id: string;
  name: string;
  kind: string;
  currency_default: string;
  lot_control_enabled: boolean;
  serial_tracking_enabled: boolean;
};

type Member = {
  id: string;
  user_id: string;
  email: string;
  name: string;
  role: "owner" | "admin" | "member" | "viewer";
  status: "active" | "invited" | "disabled";
};

type Invitation = {
  id: string;
  email: string;
  role: string;
  status: "pending" | "accepted" | "revoked";
  token: string | null;
  created_at: string;
};

export default function WorkspaceSettings() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const { data: cur } = useQuery({ queryKey: ["ws", "current"], queryFn: () => api.get<Ws>("/workspaces/current") });
  const { data: members, refetch: refetchMembers } = useQuery({
    queryKey: ["ws", "members"],
    queryFn: () => api.get<Member[]>("/workspaces/members"),
  });
  const { data: invites, refetch: refetchInvites } = useQuery({
    queryKey: ["ws", "invitations"],
    queryFn: () => api.get<Invitation[]>("/invitations"),
  });
  const [newName, setNewName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"admin" | "member" | "viewer">("member");
  const [err, setErr] = useState<string | null>(null);

  async function createWs() {
    if (!newName.trim()) return;
    await api.post("/workspaces", { name: newName.trim() });
    setNewName("");
    qc.invalidateQueries();
    window.location.reload();
  }

  async function patch(body: Partial<Ws>) {
    setErr(null);
    try {
      await api.patch("/workspaces/current", body);
      qc.invalidateQueries({ queryKey: ["ws", "current"] });
      toast.success("Workspace settings saved.");
    } catch (e) {
      const m = e instanceof ApiError ? e.message : "Failed";
      setErr(m);
      toast.error(m);
    }
  }

  async function invite() {
    if (!inviteEmail.trim()) return;
    setErr(null);
    try {
      await api.post("/invitations", { email: inviteEmail.trim(), role: inviteRole });
      const sent = inviteEmail.trim();
      setInviteEmail("");
      refetchInvites();
      toast.success(`Invitation sent to ${sent}.`);
    } catch (e) {
      const m = e instanceof ApiError ? e.message : "Failed";
      setErr(m);
      toast.error(m);
    }
  }

  async function patchMember(id: string, body: Partial<Member>) {
    setErr(null);
    try {
      await api.patch(`/workspaces/members/${id}`, body);
      refetchMembers();
      toast.success("Member updated.");
    } catch (e) {
      const m = e instanceof ApiError ? e.message : "Failed";
      setErr(m);
      toast.error(m);
    }
  }

  async function removeMember(id: string) {
    if (!confirm("Remove this member from the workspace?")) return;
    setErr(null);
    try {
      await api.delete(`/workspaces/members/${id}`);
      refetchMembers();
      toast.success("Member removed.");
    } catch (e) {
      const m = e instanceof ApiError ? e.message : "Failed";
      setErr(m);
      toast.error(m);
    }
  }

  async function revokeInvite(id: string) {
    if (!confirm("Revoke this invitation?")) return;
    await api.delete(`/invitations/${id}`);
    refetchInvites();
    toast.success("Invitation revoked.");
  }

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-semibold mb-4">Workspace</h1>
      {err && <div className="card p-3 text-danger text-sm mb-3">{err}</div>}
      {cur && (
        <div className="card p-4 mb-4 space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Name</label>
              <input
                className="input"
                defaultValue={cur.name}
                onBlur={e => e.target.value && e.target.value !== cur.name && patch({ name: e.target.value })}
              />
            </div>
            <div>
              <label className="label">Default currency</label>
              <input
                className="input"
                maxLength={3}
                defaultValue={cur.currency_default}
                onBlur={e => {
                  const v = e.target.value.toUpperCase();
                  if (v && v.length === 3 && v !== cur.currency_default) patch({ currency_default: v });
                }}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={cur.lot_control_enabled}
                onChange={e => patch({ lot_control_enabled: e.target.checked })}
              />
              Lot control
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={cur.serial_tracking_enabled}
                onChange={e => patch({ serial_tracking_enabled: e.target.checked })}
              />
              Serial tracking
            </label>
          </div>
          {cur.serial_tracking_enabled && (
            <div className="text-xs text-muted">
              When on, parts marked <em>serialized</em> require <code>lot.serial_number</code>
              {" "}and quantity 1 per add-stock or receive line.
            </div>
          )}
          <div><span className="text-muted">Kind:</span> {cur.kind}</div>
        </div>
      )}

      <h2 className="text-md font-semibold mb-2">Members</h2>
      <div className="card p-4 mb-4">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Role</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {(members ?? []).map(m => (
              <tr key={m.id}>
                <td>{m.name}</td>
                <td className="font-mono text-xs">{m.email}</td>
                <td>
                  <select
                    className="input"
                    value={m.role}
                    onChange={e => patchMember(m.id, { role: e.target.value as Member["role"] })}
                  >
                    <option value="owner">owner</option>
                    <option value="admin">admin</option>
                    <option value="member">member</option>
                    <option value="viewer">viewer</option>
                  </select>
                </td>
                <td>{m.status}</td>
                <td><button className="btn-danger text-xs" onClick={() => removeMember(m.id)}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 className="text-md font-semibold mb-2">Invitations</h2>
      <div className="card p-4 mb-4 space-y-3">
        <div className="flex gap-2 items-end">
          <div className="flex-1">
            <label className="label">Email</label>
            <input className="input" type="email" value={inviteEmail} onChange={e => setInviteEmail(e.target.value)} placeholder="teammate@example.com" />
          </div>
          <div>
            <label className="label">Role</label>
            <select className="input" value={inviteRole} onChange={e => setInviteRole(e.target.value as any)}>
              <option value="admin">admin</option>
              <option value="member">member</option>
              <option value="viewer">viewer</option>
            </select>
          </div>
          <button className="btn-primary" onClick={invite}>Invite</button>
        </div>
        {(invites ?? []).filter(i => i.status === "pending").length > 0 && (
          <table className="table">
            <thead>
              <tr><th>Email</th><th>Role</th><th>Token</th><th></th></tr>
            </thead>
            <tbody>
              {(invites ?? []).filter(i => i.status === "pending").map(inv => (
                <tr key={inv.id}>
                  <td>{inv.email}</td>
                  <td>{inv.role}</td>
                  <td>
                    <code className="text-xs break-all">{inv.token}</code>
                    <div className="text-xs text-muted mt-1">
                      Send the invitee this token; they paste it on the Account page after signing up.
                    </div>
                  </td>
                  <td><button className="btn-danger text-xs" onClick={() => revokeInvite(inv.id)}>Revoke</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <h2 className="text-md font-semibold mb-2">All workspaces</h2>
      <ul className="space-y-1 mb-4">
        {me?.workspaces.map(w => (
          <li key={w.id} className="text-sm card p-3 flex items-center justify-between">
            <span>{w.name} <span className="pill ml-2">{w.kind}</span></span>
            <span className="font-mono text-xs text-muted">{w.id}</span>
          </li>
        ))}
      </ul>
      <h2 className="text-md font-semibold mb-2">Create new workspace</h2>
      <div className="flex gap-2">
        <input className="input max-w-xs" placeholder="Workspace name" value={newName} onChange={e => setNewName(e.target.value)} />
        <button className="btn-primary" onClick={createWs}>Create</button>
      </div>
    </div>
  );
}
