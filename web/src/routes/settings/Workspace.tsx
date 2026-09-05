import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useConfirm } from "@/components/ConfirmDialog";
import { InlineQueryError } from "@/components/QueryStateBoundary";
import { ActiveListsCard } from "./ActiveListsCard";
import { ProvidersCard, type ProviderCredential } from "./ProvidersCard";
import { SourcingCard } from "./SourcingCard";

type Ws = {
  id: string;
  name: string;
  kind: string;
  currency_default: string;
  lot_control_enabled: boolean;
  serial_tracking_enabled: boolean;
  catalog_enabled: boolean;
  /** True when a catalog token has been generated (hash is stored). */
  catalog_token_set: boolean;
  /**
   * Returned exactly once when a token is freshly minted or regenerated.
   * The frontend must present a copy-once modal; subsequent GET responses
   * will NOT include this field (SEC2-008).
   */
  catalog_token_plaintext?: string;
  parts_provider: "none" | "mouser" | "digikey";
  has_parts_provider_api_key: boolean;
  has_parts_provider_api_secret: boolean;
  // Secondary providers configured in workspace_provider_credentials —
  // presence flags only, never the values. See ProvidersCard.
  provider_credentials: ProviderCredential[];
  sourcing_provider: "none" | "trustedparts";
  sourcing_country_code: string | null;
  sourcing_currency_code: string | null;
  sourcing_language_code: "de" | "en" | "es" | "fr" | "it" | "pt" | "ja" | "zh-hans" | "zh-hant" | null;
  sourcing_preferred_distributors: string[] | null;
  active_currencies: string[];
  active_countries: string[];
  active_distributors: string[];
  sourcing_use_cached_for_dashboards: boolean;
  has_sourcing_company_id: boolean;
  has_sourcing_api_key: boolean;
  scanner: "zxing" | "scandit";
  has_scanner_license_key: boolean;
};

type CatalogToken = {
  id: string;
  label: string;
  created_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  /** Present only at creation time — never returned by list. */
  token?: string;
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
  const confirm = useConfirm();
  const { me, workspaceId, refresh, switchWorkspace } = useAuth();
  const qc = useQueryClient();
  const curQuery = useQuery({ queryKey: useWsKey("ws", "current"), queryFn: ({ signal }) => api.get<Ws>("/workspaces/current", { signal }) });
  const membersQuery = useQuery({
    queryKey: useWsKey("ws", "members"),
    queryFn: ({ signal }) => api.get<Member[]>("/workspaces/members", { signal }),
  });
  const invitesQuery = useQuery({
    queryKey: useWsKey("ws", "invitations"),
    queryFn: ({ signal }) => api.get<Invitation[]>("/invitations", { signal }),
  });
  const catalogTokensQuery = useQuery({
    queryKey: useWsKey("ws", "catalog-tokens"),
    queryFn: ({ signal }) => api.get<CatalogToken[]>("/workspaces/current/catalog/tokens", { signal }),
  });
  const { data: cur } = curQuery;
  const { data: members, refetch: refetchMembers } = membersQuery;
  const { data: invites, refetch: refetchInvites } = invitesQuery;
  const { data: catalogTokens, refetch: refetchCatalogTokens } = catalogTokensQuery;
  const [newTokenLabel, setNewTokenLabel] = useState("");
  const [newlyCreatedToken, setNewlyCreatedToken] = useState<string | null>(null);

  const createCatalogToken = useMutation({
    mutationFn: (label: string) =>
      api.post<CatalogToken>("/workspaces/current/catalog/tokens", { label }),
    onSuccess: (data) => {
      if (data?.token) {
        setNewlyCreatedToken(data.token);
      }
      setNewTokenLabel("");
      refetchCatalogTokens();
      toast.success("Catalog token created.");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.userMessage : "Failed"),
  });

  const revokeCatalogToken = useMutation({
    mutationFn: (id: string) =>
      api.delete(`/workspaces/current/catalog/tokens/${id}`),
    onSuccess: () => {
      refetchCatalogTokens();
      toast.success("Token revoked.");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.userMessage : "Failed"),
  });

  const [newName, setNewName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"admin" | "member" | "viewer">("member");
  const [providerKey, setProviderKey] = useState("");
  const [providerSecret, setProviderSecret] = useState("");
  const [scannerLicense, setScannerLicense] = useState("");
  const [err, setErr] = useState<string | null>(null);

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

  const scannerKeyMutation = useApiMutation<unknown, { scanner_license_key: string }>({
    mutationKey: ["workspace", "scanner-key"],
    mutationFn: (body) => api.patch("/workspaces/current", body),
    onSuccess: (_, body) => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "ws", "current") });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "ws", "scanner", "license-key") });
      setScannerLicense("");
      toast.success(body.scanner_license_key ? "License key saved." : "License key cleared.");
    },
    onError: (e) => {
      toast.error(e instanceof ApiError ? e.userMessage : "Failed");
    },
  });

  const providerKeyBusy = providerKeyMutation.isPending;
  const scannerBusy = scannerKeyMutation.isPending;
  /**
   * SEC2-008 copy-once token: when the backend returns catalog_token_plaintext
   * (freshly minted or regenerated token) we stash it here and render a
   * copy-once banner.  The field is cleared once the user copies it or
   * dismisses the banner.  After that the token is gone — regenerate to get
   * a new one.
   */
  const [pendingToken, setPendingToken] = useState<string | null>(null);

  async function createWs() {
    if (!newName.trim()) return;
    const created = await api.post<{ id: string }>("/workspaces", { name: newName.trim() });
    setNewName("");
    // Refresh /auth/me so the picker lists the new workspace, then
    // hop into it through the workspace-switch path. This replaces a
    // full `window.location.reload()` (FE2-003).
    await refresh();
    if (created?.id) {
      await switchWorkspace(created.id);
    } else {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "ws", "current") });
    }
  }

  async function patch(body: Partial<Omit<Ws, "catalog_token_set" | "catalog_token_plaintext">> & { regenerate_catalog_token?: boolean }) {
    setErr(null);
    try {
      const result = await api.patch<Ws>("/workspaces/current", body);
      // SEC2-008: if the server returned a freshly minted/rotated token,
      // surface it once in the copy-once UI before invalidating the cache
      // (the invalidation re-fetches and the new response won't carry the
      // plaintext).
      if (result?.catalog_token_plaintext) {
        setPendingToken(result.catalog_token_plaintext);
      }
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "ws", "current") });
      toast.success("Workspace settings saved.");
    } catch (e) {
      const m = e instanceof ApiError ? e.userMessage : "Failed";
      setErr(m);
      toast.error(m);
    }
  }

  async function copyToClipboard(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Copied to clipboard.");
    } catch {
      toast.error("Could not copy — your browser may not support clipboard access.");
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
      const m = e instanceof ApiError ? e.userMessage : "Failed";
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
      const m = e instanceof ApiError ? e.userMessage : "Failed";
      setErr(m);
      toast.error(m);
    }
  }

  async function removeMember(id: string) {
    if (!(await confirm({
      message: "Remove this member from the workspace?",
      severity: "danger",
      confirmLabel: "Remove",
    }))) return;
    setErr(null);
    try {
      await api.delete(`/workspaces/members/${id}`);
      refetchMembers();
      toast.success("Member removed.");
    } catch (e) {
      const m = e instanceof ApiError ? e.userMessage : "Failed";
      setErr(m);
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
    refetchInvites();
    toast.success("Invitation revoked.");
  }

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-semibold mb-4">Workspace</h1>
      {err && <div className="card p-3 text-danger text-sm mb-3">{err}</div>}
      <div className="space-y-2 mb-3">
        <InlineQueryError query={curQuery} label="workspace settings" />
        <InlineQueryError query={membersQuery} label="members" />
        <InlineQueryError query={invitesQuery} label="invitations" />
        <InlineQueryError query={catalogTokensQuery} label="catalog tokens" />
      </div>
      {cur && (
        <div className="card p-4 mb-4 space-y-3 text-sm">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="label" htmlFor="workspace-name">Name</label>
              <input
                id="workspace-name"
                className="input"
                defaultValue={cur.name}
                onBlur={e => e.target.value && e.target.value !== cur.name && patch({ name: e.target.value })}
              />
            </div>
            <div>
              <label className="label" htmlFor="workspace-currency">Default currency</label>
              <input
                id="workspace-currency"
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
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
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

      {cur && (
        <div className="card p-4 mb-4 space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-md font-semibold">Public catalog</h2>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={cur.catalog_enabled}
                onChange={e => patch({ catalog_enabled: e.target.checked })}
              />
              Enabled
            </label>
          </div>
          {/* Copy-once banner: shown immediately after token generation/rotation */}
          {pendingToken && (
            <div className="rounded border border-warning bg-warning/10 p-3 space-y-2">
              <div className="text-xs font-semibold text-warning-foreground">
                Your catalog URL — copy it now. It will not be shown again.
              </div>
              <div className="flex gap-2 items-center">
                <input
                  className="input flex-1 font-mono text-xs"
                  readOnly
                  value={`${window.location.origin}/catalog/${pendingToken}`}
                />
                <button
                  className="btn-primary"
                  type="button"
                  onClick={() => {
                    copyToClipboard(`${window.location.origin}/catalog/${pendingToken}`);
                    setPendingToken(null);
                  }}
                >
                  Copy &amp; dismiss
                </button>
                <button
                  className="btn"
                  type="button"
                  onClick={() => setPendingToken(null)}
                >
                  Dismiss
                </button>
              </div>
            </div>
          )}
          {cur.catalog_enabled ? (
            <>
              <div className="text-xs text-muted">
                Anyone with the catalog link can browse parts you've marked as{" "}
                <em>published</em>. No login required. The token is never shown
                after generation — regenerate to rotate it. The old URL stops
                working immediately on rotation.
              </div>
              <div className="flex gap-2 items-center">
                {cur.catalog_token_set ? (
                  <span className="text-xs text-muted font-mono">
                    Token set — copy it from the banner above, or regenerate below.
                  </span>
                ) : (
                  <span className="text-xs text-muted">
                    No token yet — enable the catalog to generate one.
                  </span>
                )}
                <button
                  className="btn-ghost ml-auto"
                  onClick={async () => {
                    if (!(await confirm({
                      message: "Regenerate the catalog token? The current URL will stop working immediately.",
                      severity: "warning",
                      confirmLabel: "Regenerate",
                    }))) return;
                    patch({ regenerate_catalog_token: true, catalog_enabled: true });
                  }}
                  type="button"
                >
                  Regenerate
                </button>
              </div>
            </>
          ) : (
            <div className="text-xs text-muted">Public catalog is off.</div>
          )}
        </div>
      )}

      {/* Catalog tokens section (SEC2-019) */}
      <div className="card p-4 mb-4 space-y-3 text-sm">
        <h2 className="text-md font-semibold">Catalog tokens</h2>
        <div className="text-xs text-muted">
          Create per-recipient tokens so individual recipients can be
          revoked without rotating all consumers. Each token provides
          access to this workspace's public catalog (when enabled above).
          The plaintext is shown <strong>once</strong> at creation — copy
          it immediately.
        </div>

        {/* Copy-once banner for newly created token */}
        {newlyCreatedToken && (
          <div className="rounded border border-warning bg-warning/10 p-3 space-y-2">
            <div className="text-xs font-semibold text-warning-foreground">
              New catalog token — copy it now. It will not be shown again.
            </div>
            <div className="flex gap-2 items-center">
              <input
                className="input flex-1 font-mono text-xs"
                readOnly
                value={`${window.location.origin}/catalog/${newlyCreatedToken}`}
              />
              <button
                className="btn-primary"
                type="button"
                onClick={() => {
                  copyToClipboard(`${window.location.origin}/catalog/${newlyCreatedToken}`);
                  setNewlyCreatedToken(null);
                }}
              >
                Copy &amp; dismiss
              </button>
              <button
                className="btn"
                type="button"
                onClick={() => setNewlyCreatedToken(null)}
              >
                Dismiss
              </button>
            </div>
          </div>
        )}

        {/* Token list */}
        {catalogTokens && catalogTokens.length > 0 && (
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
              {catalogTokens.map((t) => (
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
                        className="btn-danger text-xs"
                        type="button"
                        disabled={revokeCatalogToken.isPending}
                        onClick={async () => {
                          if (!(await confirm({
                            message: `Revoke the token "${t.label}"? Any consumer using it will immediately lose access.`,
                            severity: "danger",
                            confirmLabel: "Revoke",
                          }))) return;
                          revokeCatalogToken.mutate(t.id);
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

        {/* Create token form */}
        <div className="flex gap-2 items-end">
          <div className="flex-1">
            <label className="label">Label (recipient name)</label>
            <input
              className="input"
              placeholder="e.g. partner-api, internal-docs"
              value={newTokenLabel}
              onChange={(e) => setNewTokenLabel(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && newTokenLabel.trim()) {
                  createCatalogToken.mutate(newTokenLabel.trim());
                }
              }}
            />
          </div>
          <button
            className="btn-primary"
            type="button"
            disabled={createCatalogToken.isPending || !newTokenLabel.trim()}
            onClick={() => {
              if (newTokenLabel.trim()) createCatalogToken.mutate(newTokenLabel.trim());
            }}
          >
            Create token
          </button>
        </div>
      </div>

      {cur && (
        <div className="card p-4 mb-4 space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-md font-semibold">Parts data provider</h2>
            <select
              className="input max-w-[160px]"
              value={cur.parts_provider}
              onChange={e => patch({ parts_provider: e.target.value as Ws["parts_provider"] })}
            >
              <option value="none">None</option>
              <option value="mouser">Mouser</option>
              <option value="digikey">DigiKey</option>
            </select>
          </div>
          {cur.parts_provider === "none" ? (
            <div className="text-xs text-muted">
              No external lookup. Pick a provider above to enable the
              <strong className="ml-1">Lookup</strong> button on linked-type parts.
            </div>
          ) : cur.parts_provider === "digikey" ? (
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
                  value={providerKey}
                  onChange={e => setProviderKey(e.target.value)}
                  placeholder={cur.has_parts_provider_api_key ? "•••••••• (Client ID set)" : "Client ID"}
                />
                <input
                  className="input flex-1 font-mono text-xs"
                  type="password"
                  autoComplete="off"
                  value={providerSecret}
                  onChange={e => setProviderSecret(e.target.value)}
                  placeholder={cur.has_parts_provider_api_secret ? "•••••••• (Secret set)" : "Client Secret"}
                />
                <button
                  type="button"
                  className="btn-primary"
                  disabled={providerKeyBusy || (!providerKey && !providerSecret)}
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
                {(cur.has_parts_provider_api_key || cur.has_parts_provider_api_secret) && (
                  <button
                    type="button"
                    className="btn"
                    disabled={providerKeyBusy}
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
                {cur.has_parts_provider_api_key ? (
                  <>API key is set. Paste a new value below to replace it, or empty to clear.</>
                ) : (
                  <>Paste your <strong className="text-text">{cur.parts_provider}</strong> Search API key.</>
                )}
              </div>
              <div className="flex gap-2 items-center">
                <input
                  className="input flex-1 font-mono text-xs"
                  type="password"
                  autoComplete="off"
                  value={providerKey}
                  onChange={e => setProviderKey(e.target.value)}
                  placeholder={cur.has_parts_provider_api_key ? "•••••••• (set)" : "API key"}
                />
                <button
                  type="button"
                  className="btn-primary"
                  disabled={providerKeyBusy}
                  onClick={() => {
                    providerKeyMutation.mutate({ parts_provider_api_key: providerKey });
                  }}
                >
                  {providerKey ? "Save" : (cur.has_parts_provider_api_key ? "Clear" : "Save")}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      <div className="card p-4 mb-4 space-y-2 text-sm">
        <h2 className="text-md font-semibold">Categories</h2>
        <div className="text-xs text-muted">
          Buckets for the parts library, with the reference-designator prefix
          and default symbol / footprint references a KiCad library is built
          from.
        </div>
        <Link to="/settings/categories" className="btn inline-flex w-fit">
          Manage categories
        </Link>
      </div>

      <div className="card p-4 mb-4 space-y-2 text-sm">
        <h2 className="text-md font-semibold">API tokens</h2>
        <div className="text-xs text-muted">
          Personal access tokens for KiCad, scripts and agents. Each one acts as
          the person who created it and can never exceed their role.
        </div>
        <Link to="/settings/api-tokens" className="btn inline-flex w-fit">
          Manage API tokens
        </Link>
      </div>

      <div className="card p-4 mb-4 space-y-2 text-sm">
        <h2 className="text-md font-semibold">KiCad setup</h2>
        <div className="text-xs text-muted">
          Connect KiCad to this workspace: the HTTP library file, the add-on
          repository that installs the symbol and footprint files, and the
          SPICE path variable.
        </div>
        <Link to="/settings/kicad" className="btn inline-flex w-fit">
          Set up KiCad
        </Link>
      </div>

      <div className="card p-4 mb-4 space-y-2 text-sm">
        <h2 className="text-md font-semibold">Label templates</h2>
        <div className="text-xs text-muted">
          Design the labels the cab SQUIX printer produces for parts, lots,
          bins, orders and builds. Each type has one default, which is what the
          Print label action uses.
        </div>
        <Link to="/settings/label-templates" className="btn inline-flex w-fit">
          Open label designer
        </Link>
      </div>

      {cur && <ProvidersCard workspace={cur} workspaceId={workspaceId} />}
      {cur && <SourcingCard workspace={cur} workspaceId={workspaceId} />}
      {cur && <ActiveListsCard workspace={cur} workspaceId={workspaceId} />}

      {cur && (
        <div className="card p-4 mb-4 space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-md font-semibold">Scanner</h2>
            <select
              className="input max-w-[220px]"
              value={cur.scanner}
              onChange={e => patch({ scanner: e.target.value as Ws["scanner"] })}
            >
              <option value="zxing">Open-source (ZXing)</option>
              <option value="scandit">Scandit (license required)</option>
            </select>
          </div>
          {cur.scanner === "zxing" ? (
            <div className="text-xs text-muted">
              Royalty-free decoder bundled with the app. Decodes Code128, Code39,
              QR, DataMatrix, and PDF417. No license key needed.
            </div>
          ) : (
            <>
              <div className="text-xs text-muted">
                Paste your Scandit license key. The key must list this site's
                origin (the page you're on right now) in its allowed domains —
                otherwise the SDK refuses to load. Empty the field to clear it.
              </div>
              <div className="flex gap-2 items-center">
                <input
                  className="input flex-1 font-mono text-xs"
                  type="password"
                  autoComplete="off"
                  value={scannerLicense}
                  onChange={e => setScannerLicense(e.target.value)}
                  placeholder={cur.has_scanner_license_key ? "•••••••• (key set)" : "license key"}
                />
                <button
                  type="button"
                  className="btn-primary"
                  disabled={scannerBusy}
                  onClick={() => {
                    scannerKeyMutation.mutate({ scanner_license_key: scannerLicense });
                  }}
                >
                  {scannerLicense ? "Save" : (cur.has_scanner_license_key ? "Clear" : "Save")}
                </button>
              </div>
            </>
          )}
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
