import { toast } from "sonner";
import { useConfirm } from "@/components/ConfirmDialog";
import { api, ApiError } from "@/lib/api";

export type Member = {
  id: string;
  user_id: string;
  email: string;
  name: string;
  role: "owner" | "admin" | "member" | "viewer";
  status: "active" | "invited" | "disabled";
};

type Props = {
  members: Member[] | undefined;
  refetch: () => void;
  onError: (message: string | null) => void;
};

/**
 * Workspace members. Heading lives inside the card, like every other section
 * on this page — Members / Invitations / All workspaces / Create workspace
 * used to put theirs outside, which read as four orphan labels.
 */
export function MembersCard({ members, refetch, onError }: Props) {
  const confirm = useConfirm();

  async function patchMember(id: string, body: Partial<Member>) {
    onError(null);
    try {
      await api.patch(`/workspaces/members/${id}`, body);
      refetch();
      toast.success("Member updated.");
    } catch (e) {
      const m = e instanceof ApiError ? e.userMessage : "Failed";
      onError(m);
      toast.error(m);
    }
  }

  async function removeMember(id: string) {
    if (!(await confirm({
      message: "Remove this member from the workspace?",
      severity: "danger",
      confirmLabel: "Remove",
    }))) return;
    onError(null);
    try {
      await api.delete(`/workspaces/members/${id}`);
      refetch();
      toast.success("Member removed.");
    } catch (e) {
      const m = e instanceof ApiError ? e.userMessage : "Failed";
      onError(m);
      toast.error(m);
    }
  }

  return (
    <div className="card p-4 mb-4 space-y-3">
      <h2 className="card-title">Members</h2>
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
                  aria-label={`Role for ${m.email}`}
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
              <td><button className="btn-danger btn-sm" onClick={() => removeMember(m.id)}>Remove</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
