import { useState } from "react";
import { toast } from "sonner";
import { useConfirm } from "@/components/ConfirmDialog";
import { api, ApiError } from "@/lib/api";

export type Invitation = {
  id: string;
  email: string;
  role: string;
  status: "pending" | "accepted" | "revoked";
  token: string | null;
  created_at: string;
};

type Props = {
  invitations: Invitation[] | undefined;
  refetch: () => void;
  onError: (message: string | null) => void;
};

export function InvitationsCard({ invitations, refetch, onError }: Props) {
  const confirm = useConfirm();
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"admin" | "member" | "viewer">("member");
  const pending = (invitations ?? []).filter(i => i.status === "pending");

  async function invite() {
    if (!inviteEmail.trim()) return;
    onError(null);
    try {
      await api.post("/invitations", { email: inviteEmail.trim(), role: inviteRole });
      const sent = inviteEmail.trim();
      setInviteEmail("");
      refetch();
      toast.success(`Invitation sent to ${sent}.`);
    } catch (e) {
      const m = e instanceof ApiError ? e.userMessage : "Failed";
      onError(m);
      toast.error(m);
    }
  }

  async function revokeInvite(id: string) {
    if (!(await confirm({
      message: "Revoke this invitation?",
      severity: "danger",
      confirmLabel: "Revoke",
    }))) return;
    await api.delete(`/invitations/${id}`);
    refetch();
    toast.success("Invitation revoked.");
  }

  return (
    <div className="card p-4 mb-4 space-y-3">
      <h2 className="card-title">Invitations</h2>
      <div className="flex gap-2 items-end">
        <div className="flex-1">
          <label className="label" htmlFor="invite-email">Email</label>
          <input id="invite-email" className="input" type="email" value={inviteEmail} onChange={e => setInviteEmail(e.target.value)} placeholder="teammate@example.com" />
        </div>
        <div>
          <label className="label" htmlFor="invite-role">Role</label>
          <select id="invite-role" className="input" value={inviteRole} onChange={e => setInviteRole(e.target.value as "admin" | "member" | "viewer")}>
            <option value="admin">admin</option>
            <option value="member">member</option>
            <option value="viewer">viewer</option>
          </select>
        </div>
        <button className="btn-primary" onClick={invite}>Invite</button>
      </div>
      {pending.length > 0 && (
        <table className="table">
          <thead>
            <tr><th>Email</th><th>Role</th><th>Token</th><th></th></tr>
          </thead>
          <tbody>
            {pending.map(inv => (
              <tr key={inv.id}>
                <td>{inv.email}</td>
                <td>{inv.role}</td>
                <td>
                  <code className="text-xs break-all">{inv.token}</code>
                  <div className="text-xs text-muted mt-1">
                    Send the invitee this token; they paste it on the Account page after signing up.
                  </div>
                </td>
                <td><button className="btn-danger btn-sm" onClick={() => revokeInvite(inv.id)}>Revoke</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
