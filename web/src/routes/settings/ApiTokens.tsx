import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApiMutation } from "@/lib/mutations";
import { ApiTokensListSchema } from "@/lib/schemas";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useConfirm } from "@/components/ConfirmDialog";
import { Modal } from "@/components/Modal";
import { InlineQueryError } from "@/components/QueryStateBoundary";
import type { ApiToken, ApiTokenCreated } from "@/types";

type CreateBody = {
  label: string;
  read_only: boolean;
  expires_in_days: number | null;
};

type Member = { user_id: string; role: string };

const ADMIN_ROLES = new Set(["admin", "owner"]);

function formatWhen(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleDateString();
}

export default function ApiTokensSettings() {
  const confirm = useConfirm();
  const qc = useQueryClient();
  const { me, workspaceId } = useAuth();
  const [showAll, setShowAll] = useState(false);
  const [creating, setCreating] = useState(false);
  const [label, setLabel] = useState("");
  const [readOnly, setReadOnly] = useState(false);
  const [expiryDays, setExpiryDays] = useState("");
  const [err, setErr] = useState<string | null>(null);
  // The plaintext lives in component state and nowhere else — it is never
  // written to the query cache, so it can't reappear on a later render.
  const [minted, setMinted] = useState<ApiTokenCreated | null>(null);

  // The role isn't on /auth/me, so read it off the membership list the
  // workspace settings page already loads (same query key, so the two
  // share one cache entry).
  const membersQuery = useQuery({
    queryKey: useWsKey("ws", "members"),
    queryFn: ({ signal }) => api.get<Member[]>("/workspaces/members", { signal }),
  });
  const isAdmin = useMemo(() => {
    const myId = me?.user?.id;
    if (!myId) return false;
    const mine = (membersQuery.data ?? []).find(m => m.user_id === myId);
    return mine ? ADMIN_ROLES.has(mine.role) : false;
  }, [me, membersQuery.data]);

  const listAll = showAll && isAdmin;
  const tokensQuery = useQuery({
    queryKey: useWsKey("api-tokens", { all: listAll }),
    queryFn: ({ signal }) =>
      api.parsed.get(`/tokens${listAll ? "?all=true" : ""}`, ApiTokensListSchema, { signal }),
  });
  const tokens = tokensQuery.data ?? [];

  function invalidate() {
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "api-tokens") });
  }

  function closeModal() {
    setCreating(false);
    setLabel("");
    setReadOnly(false);
    setExpiryDays("");
    setErr(null);
  }

  const createMutation = useApiMutation<ApiTokenCreated, CreateBody>({
    mutationKey: ["api-tokens", "create"],
    mutationFn: (body) => api.post<ApiTokenCreated, CreateBody>("/tokens", body),
    onSuccess: (created) => {
      invalidate();
      setMinted(created);
      closeModal();
    },
    onError: (e) => {
      const message = e instanceof ApiError ? e.userMessage : "Failed";
      setErr(message);
      toast.error(message);
    },
  });

  const revokeMutation = useApiMutation<unknown, string>({
    mutationKey: ["api-tokens", "revoke"],
    mutationFn: (id) => api.post(`/tokens/${id}/revoke`),
    onSuccess: () => {
      invalidate();
      toast.success("Token revoked.");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.userMessage : "Failed"),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!label.trim()) {
      setErr("Label is required.");
      return;
    }
    const days = expiryDays.trim() === "" ? null : Number(expiryDays);
    if (days !== null && (!Number.isInteger(days) || days < 1 || days > 365)) {
      setErr("Expiry must be a whole number of days between 1 and 365.");
      return;
    }
    createMutation.mutate({ label: label.trim(), read_only: readOnly, expires_in_days: days });
  }

  async function revoke(token: ApiToken) {
    const ok = await confirm({
      title: `Revoke "${token.label}"?`,
      message:
        "Anything still using this token stops working immediately. Revoking cannot be undone — mint a new token instead.",
      severity: "danger",
      confirmLabel: "Revoke",
    });
    if (!ok) return;
    revokeMutation.mutate(token.id);
  }

  async function copyPlaintext(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("Token copied to clipboard.");
    } catch {
      // Clipboard access is denied in plenty of legitimate contexts
      // (insecure origin, permissions policy). The token is on screen
      // and selectable, so this is a convenience, not the only route.
      toast.error("Couldn't copy — select the token and copy it manually.");
    }
  }

  return (
    <div className="max-w-4xl">
      <h1 className="text-xl font-semibold mb-4">API tokens</h1>
      <p className="text-sm text-muted mb-4">
        Personal access tokens let KiCad, scripts and agents reach this
        workspace without a browser session. A token acts as you, in this
        workspace only, and can never do more than your role allows.
      </p>

      <InlineQueryError query={tokensQuery} label="API tokens" className="mb-3" />

      {minted && (
        <div className="card p-4 mb-4 space-y-2 border-warning/50">
          <h2 className="text-md font-semibold">Copy your new token now</h2>
          <p className="text-sm text-muted">
            This is the only time it will ever be shown. We store a one-way hash,
            so it cannot be recovered — if you lose it, revoke it and mint another.
          </p>
          <code
            className="block font-mono text-xs break-all rounded bg-panel2 p-2"
            data-testid="minted-token"
          >
            {minted.token}
          </code>
          <div className="flex gap-2">
            <button type="button" className="btn-primary" onClick={() => copyPlaintext(minted.token)}>
              Copy
            </button>
            <button type="button" className="btn" onClick={() => setMinted(null)}>
              I&apos;ve saved it
            </button>
          </div>
        </div>
      )}

      <div className="card p-4 space-y-3">
        <div className="flex items-center gap-3">
          {isAdmin && (
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={showAll}
                onChange={e => setShowAll(e.target.checked)}
              />
              Show everyone&apos;s tokens
            </label>
          )}
          <button type="button" className="btn-primary ml-auto" onClick={() => setCreating(true)}>
            + Token
          </button>
        </div>

        {tokensQuery.isLoading ? (
          <div className="text-muted text-sm">Loading…</div>
        ) : tokens.length === 0 ? (
          <div className="text-muted text-sm">
            No tokens yet. Mint one to connect KiCad or a script.
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Label</th>
                {listAll && <th>Owner</th>}
                <th>Created</th>
                <th>Last used</th>
                <th>Expires</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {tokens.map(token => (
                <tr key={token.id} className={token.revoked_at ? "opacity-50" : ""}>
                  <td>
                    <span className="font-medium">{token.label}</span>
                    {token.read_only && <span className="pill ml-2 text-xs">Read-only</span>}
                    {token.revoked_at && <span className="pill ml-2 text-xs">Revoked</span>}
                  </td>
                  {listAll && <td className="text-xs">{token.user_email ?? "—"}</td>}
                  <td>{formatWhen(token.created_at)}</td>
                  <td>{formatWhen(token.last_used_at)}</td>
                  <td>{token.expires_at ? formatWhen(token.expires_at) : "Never"}</td>
                  <td className="whitespace-nowrap">
                    {!token.revoked_at && (
                      <button
                        type="button"
                        className="btn-danger text-xs"
                        disabled={revokeMutation.isPending}
                        onClick={() => revoke(token)}
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Modal open={creating} onClose={closeModal} title="Create API token" size="sm">
        {/* noValidate: the `min`/`max` on the expiry field are there for
            the number spinner, but native constraint validation would
            swallow the submit and show a browser tooltip instead of our
            own message. `submit()` does the real checking. */}
        <form onSubmit={submit} className="p-4 space-y-3" noValidate>
          <h2 className="text-md font-semibold">Create API token</h2>
          {err && <div className="text-danger text-sm">{err}</div>}
          <div>
            <label className="label" htmlFor="token-label">Label</label>
            <input
              id="token-label"
              className="input"
              maxLength={120}
              placeholder="KiCad on the bench laptop"
              value={label}
              onChange={e => setLabel(e.target.value)}
            />
          </div>
          <div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={readOnly}
                onChange={e => setReadOnly(e.target.checked)}
              />
              Read-only
            </label>
            <div className="text-xs text-muted mt-1">
              Refuses every write. Use it for KiCad and anywhere the token ends
              up in a config file.
            </div>
          </div>
          <div>
            <label className="label" htmlFor="token-expiry">Expires in (days)</label>
            <input
              id="token-expiry"
              className="input"
              type="number"
              min={1}
              max={365}
              placeholder="never"
              value={expiryDays}
              onChange={e => setExpiryDays(e.target.value)}
            />
            <div className="text-xs text-muted mt-1">
              Leave blank for a token that never expires.
            </div>
          </div>
          <div className="flex gap-2">
            <button className="btn-primary" disabled={createMutation.isPending}>
              {createMutation.isPending ? "Creating…" : "Create"}
            </button>
            <button type="button" className="btn" onClick={closeModal}>
              Cancel
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
