import { useState } from "react";
import { toast } from "sonner";
import { useConfirm } from "@/components/ConfirmDialog";
import { ApiError, api } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useQueryClient } from "@tanstack/react-query";
import { wsKeyOf } from "@/lib/queryKeys";

export type PartsProviderWorkspace = {
  parts_provider: "none" | "mouser" | "digikey";
  has_parts_provider_api_key: boolean;
  has_parts_provider_api_secret: boolean;
};

type Props = {
  workspace: PartsProviderWorkspace;
  workspaceId: string | null | undefined;
  /** Patches `/workspaces/current`; owned by the page so one error banner serves every card. */
  onPatch: (body: { parts_provider: PartsProviderWorkspace["parts_provider"] }) => void;
};

/**
 * The PRIMARY parts provider and its credentials.
 *
 * Per ADR-0031 the primary's key/secret live in the legacy
 * `workspaces.parts_provider_api_*` columns, NOT in
 * `workspace_provider_credentials` — that table (and `ProvidersCard`) holds
 * secondaries only.
 */
export function PartsProviderCard({ workspace, workspaceId, onPatch }: Props) {
  const confirm = useConfirm();
  const qc = useQueryClient();
  const [providerKey, setProviderKey] = useState("");
  const [providerSecret, setProviderSecret] = useState("");

  const providerKeyMutation = useApiMutation<unknown, Record<string, string>>({
    mutationKey: ["workspace", "provider-key"],
    mutationFn: (body) => api.patch("/workspaces/current", body),
    onSuccess: (_, body) => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "ws", "current") });
      setProviderKey("");
      setProviderSecret("");
      const cleared = Object.values(body).every(v => v === "");
      toast.success(cleared ? "Credentials cleared." : "Credentials saved.");
    },
    onError: (e) => {
      toast.error(e instanceof ApiError ? e.userMessage : "Failed");
    },
  });
  const busy = providerKeyMutation.isPending;

  return (
    <div className="card p-4 mb-4 space-y-3 text-sm">
      <div className="flex items-center justify-between">
        <h2 className="card-title">Parts data provider</h2>
        <select
          className="input max-w-[160px]"
          aria-label="Parts data provider"
          value={workspace.parts_provider}
          onChange={e => onPatch({ parts_provider: e.target.value as PartsProviderWorkspace["parts_provider"] })}
        >
          <option value="none">None</option>
          <option value="mouser">Mouser</option>
          <option value="digikey">DigiKey</option>
        </select>
      </div>
      {workspace.parts_provider === "none" ? (
        <div className="text-xs text-muted">
          No external lookup. Pick a provider above to enable the
          <strong className="ml-1">Lookup</strong> button on linked-type parts.
        </div>
      ) : workspace.parts_provider === "digikey" ? (
        <>
          <div className="text-xs text-muted">
            Paste your DigiKey <strong className="text-text">Client ID</strong> and{" "}
            <strong className="text-text">Client Secret</strong> from the DigiKey
            developer portal. Both are required. Empty either field to clear it.
          </div>
          <div className="flex gap-2 items-center">
            <input
              className="input flex-1 font-mono text-xs"
              type="password"
              autoComplete="off"
              aria-label="DigiKey Client ID"
              value={providerKey}
              onChange={e => setProviderKey(e.target.value)}
              placeholder={workspace.has_parts_provider_api_key ? "•••••••• (Client ID set)" : "Client ID"}
            />
            <input
              className="input flex-1 font-mono text-xs"
              type="password"
              autoComplete="off"
              aria-label="DigiKey Client Secret"
              value={providerSecret}
              onChange={e => setProviderSecret(e.target.value)}
              placeholder={workspace.has_parts_provider_api_secret ? "•••••••• (Secret set)" : "Client Secret"}
            />
            <button
              type="button"
              className="btn-primary"
              disabled={busy || (!providerKey && !providerSecret)}
              onClick={() => {
                const body: Record<string, string> = {};
                // Only send fields the user actually changed (the
                // backend leaves omitted fields alone).
                if (providerKey) body.parts_provider_api_key = providerKey;
                if (providerSecret) body.parts_provider_api_secret = providerSecret;
                providerKeyMutation.mutate(body);
              }}
            >
              Save
            </button>
            {(workspace.has_parts_provider_api_key || workspace.has_parts_provider_api_secret) && (
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={async () => {
                  if (!(await confirm({
                    message: "Clear both DigiKey credentials?",
                    severity: "danger",
                    confirmLabel: "Clear",
                  }))) return;
                  providerKeyMutation.mutate({
                    parts_provider_api_key: "",
                    parts_provider_api_secret: "",
                  });
                }}
              >
                Clear
              </button>
            )}
          </div>
        </>
      ) : (
        <>
          <div className="text-xs text-muted">
            {workspace.has_parts_provider_api_key ? (
              <>API key is set. Paste a new value below to replace it, or empty to clear.</>
            ) : (
              <>Paste your <strong className="text-text">{workspace.parts_provider}</strong> Search API key.</>
            )}
          </div>
          <div className="flex gap-2 items-center">
            <input
              className="input flex-1 font-mono text-xs"
              type="password"
              autoComplete="off"
              aria-label="Parts provider API key"
              value={providerKey}
              onChange={e => setProviderKey(e.target.value)}
              placeholder={workspace.has_parts_provider_api_key ? "•••••••• (set)" : "API key"}
            />
            <button
              type="button"
              className="btn-primary"
              disabled={busy}
              onClick={() => {
                providerKeyMutation.mutate({ parts_provider_api_key: providerKey });
              }}
            >
              {providerKey ? "Save" : (workspace.has_parts_provider_api_key ? "Clear" : "Save")}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
