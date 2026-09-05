import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { useConfirm } from "@/components/ConfirmDialog";
import { api, ApiError } from "@/lib/api";
import { CopyOnceTokenBanner } from "./CopyOnceTokenBanner";

export type CatalogToken = {
  id: string;
  label: string;
  created_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  /** Present only at creation time — never returned by list. */
  token?: string;
};

type Props = {
  tokens: CatalogToken[] | undefined;
  refetch: () => void;
};

/**
 * Per-recipient catalog tokens (SEC2-019). Split out of Workspace.tsx, which
 * had grown to 863 lines across 15 sections.
 */
export function CatalogTokensCard({ tokens, refetch }: Props) {
  const confirm = useConfirm();
  const [newTokenLabel, setNewTokenLabel] = useState("");
  const [newlyCreatedToken, setNewlyCreatedToken] = useState<string | null>(null);

  const createToken = useMutation({
    mutationFn: (label: string) =>
      api.post<CatalogToken>("/workspaces/current/catalog/tokens", { label }),
    onSuccess: (data) => {
      if (data?.token) setNewlyCreatedToken(data.token);
      setNewTokenLabel("");
      refetch();
      toast.success("Catalog token created.");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.userMessage : "Failed"),
  });

  const revokeToken = useMutation({
    mutationFn: (id: string) => api.delete(`/workspaces/current/catalog/tokens/${id}`),
    onSuccess: () => {
      refetch();
      toast.success("Token revoked.");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.userMessage : "Failed"),
  });

  return (
    <div className="card p-4 mb-4 space-y-3 text-sm">
      <h2 className="card-title">Catalog tokens</h2>
      <div className="text-xs text-muted">
        Create per-recipient tokens so individual recipients can be
        revoked without rotating all consumers. Each token provides
        access to this workspace's public catalog (when enabled above).
        The plaintext is shown <strong>once</strong> at creation — copy
        it immediately.
      </div>

      {newlyCreatedToken && (
        <CopyOnceTokenBanner
          title="New catalog token — copy it now. It will not be shown again."
          url={`${window.location.origin}/catalog/${newlyCreatedToken}`}
          onDismiss={() => setNewlyCreatedToken(null)}
        />
      )}

      {tokens && tokens.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Label</th>
              <th>Created</th>
              <th>Last used</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {tokens.map((t) => (
              <tr key={t.id} className={t.revoked_at ? "opacity-50" : ""}>
                <td>{t.label}</td>
                <td className="text-xs text-muted">
                  {t.created_at ? new Date(t.created_at).toLocaleString() : "—"}
                </td>
                <td className="text-xs text-muted">
                  {t.last_used_at ? new Date(t.last_used_at).toLocaleString() : "Never"}
                </td>
                <td>
                  {t.revoked_at ? (
                    <span className="pill text-xs">Revoked</span>
                  ) : (
                    <span className="pill text-xs bg-success/10 text-success">Active</span>
                  )}
                </td>
                <td>
                  {!t.revoked_at && (
                    <button
                      className="btn-danger btn-sm"
                      type="button"
                      disabled={revokeToken.isPending}
                      onClick={async () => {
                        if (!(await confirm({
                          message: `Revoke the token "${t.label}"? Any consumer using it will immediately lose access.`,
                          severity: "danger",
                          confirmLabel: "Revoke",
                        }))) return;
                        revokeToken.mutate(t.id);
                      }}
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

      <div className="flex gap-2 items-end">
        <div className="flex-1">
          <label className="label" htmlFor="catalog-token-label">Label (recipient name)</label>
          <input
            id="catalog-token-label"
            className="input"
            placeholder="e.g. partner-api, internal-docs"
            value={newTokenLabel}
            onChange={(e) => setNewTokenLabel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && newTokenLabel.trim()) {
                createToken.mutate(newTokenLabel.trim());
              }
            }}
          />
        </div>
        <button
          className="btn-primary"
          type="button"
          disabled={createToken.isPending || !newTokenLabel.trim()}
          onClick={() => {
            if (newTokenLabel.trim()) createToken.mutate(newTokenLabel.trim());
          }}
        >
          Create token
        </button>
      </div>
    </div>
  );
}
