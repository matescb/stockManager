import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useWsKey, wsKeyOf, wsScope } from "@/lib/queryKeys";
import { useConfirm } from "@/components/ConfirmDialog";

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
  scanner: "zxing" | "scandit";
  has_scanner_license_key: boolean;
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
  const { data: cur } = useQuery({ queryKey: useWsKey("ws", "current"), queryFn: () => api.get<Ws>("/workspaces/current") });
  const { data: members, refetch: refetchMembers } = useQuery({
    queryKey: useWsKey("ws", "members"),
    queryFn: () => api.get<Member[]>("/workspaces/members"),
  });
  const { data: invites, refetch: refetchInvites } = useQuery({
    queryKey: useWsKey("ws", "invitations"),
    queryFn: () => api.get<Invitation[]>("/invitations"),
  });
  const [newName, setNewName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"admin" | "member" | "viewer">("member");
  const [providerKey, setProviderKey] = useState("");
  const [providerSecret, setProviderSecret] = useState("");
  const [providerKeyBusy, setProviderKeyBusy] = useState(false);
  const [scannerLicense, setScannerLicense] = useState("");
  const [scannerBusy, setScannerBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
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
      qc.invalidateQueries({ queryKey: wsScope(workspaceId) });
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
      const m = e instanceof ApiError ? e.message : "Failed";
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
      const m = e instanceof ApiError ? e.message : "Failed";
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
                  onClick={async () => {
                    setProviderKeyBusy(true);
                    try {
                      const body: Record<string, string> = {};
                      // Only send fields the user actually changed (the
                      // backend leaves omitted fields alone).
                      if (providerKey) body.parts_provider_api_key = providerKey;
                      if (providerSecret) body.parts_provider_api_secret = providerSecret;
                      await api.patch("/workspaces/current", body);
                      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "ws", "current") });
                      setProviderKey("");
                      setProviderSecret("");
                      toast.success("Credentials saved.");
                    } catch (e) {
                      toast.error(e instanceof ApiError ? e.message : "Failed");
                    } finally {
                      setProviderKeyBusy(false);
                    }
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
                      setProviderKeyBusy(true);
                      try {
                        await api.patch("/workspaces/current", {
                          parts_provider_api_key: "",
                          parts_provider_api_secret: "",
                        });
                        qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "ws", "current") });
                        toast.success("Credentials cleared.");
                      } catch (e) {
                        toast.error(e instanceof ApiError ? e.message : "Failed");
                      } finally {
                        setProviderKeyBusy(false);
                      }
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
                  onClick={async () => {
                    setProviderKeyBusy(true);
                    try {
                      await api.patch("/workspaces/current", {
                        parts_provider_api_key: providerKey,
                      });
                      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "ws", "current") });
                      setProviderKey("");
                      toast.success(providerKey ? "API key saved." : "API key cleared.");
                    } catch (e) {
                      toast.error(e instanceof ApiError ? e.message : "Failed");
                    } finally {
                      setProviderKeyBusy(false);
                    }
                  }}
                >
                  {providerKey ? "Save" : (cur.has_parts_provider_api_key ? "Clear" : "Save")}
                </button>
              </div>
            </>
          )}
        </div>
      )}

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
                  onClick={async () => {
                    setScannerBusy(true);
                    try {
                      await api.patch("/workspaces/current", {
                        scanner_license_key: scannerLicense,
                      });
                      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "ws", "current") });
                      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "ws", "scanner", "license-key") });
                      setScannerLicense("");
                      toast.success(scannerLicense ? "License key saved." : "License key cleared.");
                    } catch (e) {
                      toast.error(e instanceof ApiError ? e.message : "Failed");
                    } finally {
                      setScannerBusy(false);
                    }
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
