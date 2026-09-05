import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useConfirm } from "@/components/ConfirmDialog";
import { InlineQueryError } from "@/components/QueryStateBoundary";
import { ActiveListsCard } from "./ActiveListsCard";
import { CatalogTokensCard, type CatalogToken } from "./CatalogTokensCard";
import { CopyOnceTokenBanner } from "./CopyOnceTokenBanner";
import { InvitationsCard, type Invitation } from "./InvitationsCard";
import { MembersCard, type Member } from "./MembersCard";
import { PartsProviderCard } from "./PartsProviderCard";
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

type WorkspacePatch =
  Partial<Omit<Ws, "catalog_token_set" | "catalog_token_plaintext">>
  & { regenerate_catalog_token?: boolean };

/** One shelf link out to a sibling settings route. */
function SettingsLinkCard({
  title,
  description,
  to,
  action,
}: {
  title: string;
  description: string;
  to: string;
  action: string;
}) {
  return (
    <div className="card p-4 mb-4 space-y-2 text-sm">
      <h2 className="card-title">{title}</h2>
      <div className="text-xs text-muted">{description}</div>
      <Link to={to} className="btn inline-flex w-fit">{action}</Link>
    </div>
  );
}

/**
 * Workspace settings.
 *
 * Every section on this page is a `card p-4 mb-4` whose `card-title` heading
 * is the card's first child. That used to hold for sections 1–12 only:
 * Members, Invitations, All workspaces and Create workspace put their heading
 * *outside* the card, and the first card had no heading at all — two section
 * idioms on one 863-line page. The per-concern cards below now each own their
 * markup; this file is composition plus the queries they share.
 */
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

  const [newName, setNewName] = useState("");
  const [scannerLicense, setScannerLicense] = useState("");
  const [err, setErr] = useState<string | null>(null);

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

  async function patch(body: WorkspacePatch) {
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

  return (
    <div className="max-w-3xl">
      <h1 className="page-title mb-4">Workspace</h1>
      {err && <div className="card p-3 text-danger text-sm mb-3">{err}</div>}
      <div className="space-y-2 mb-3">
        <InlineQueryError query={curQuery} label="workspace settings" />
        <InlineQueryError query={membersQuery} label="members" />
        <InlineQueryError query={invitesQuery} label="invitations" />
        <InlineQueryError query={catalogTokensQuery} label="catalog tokens" />
      </div>

      {cur && (
        <div className="card p-4 mb-4 space-y-3 text-sm">
          <h2 className="card-title">General</h2>
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
            <h2 className="card-title">Public catalog</h2>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={cur.catalog_enabled}
                onChange={e => patch({ catalog_enabled: e.target.checked })}
              />
              Enabled
            </label>
          </div>
          {pendingToken && (
            <CopyOnceTokenBanner
              title="Your catalog URL — copy it now. It will not be shown again."
              url={`${window.location.origin}/catalog/${pendingToken}`}
              onDismiss={() => setPendingToken(null)}
            />
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

      <CatalogTokensCard tokens={catalogTokens} refetch={refetchCatalogTokens} />

      {cur && (
        <PartsProviderCard workspace={cur} workspaceId={workspaceId} onPatch={patch} />
      )}

      <SettingsLinkCard
        title="Categories"
        description="Buckets for the parts library, with the reference-designator prefix and default symbol / footprint references a KiCad library is built from."
        to="/settings/categories"
        action="Manage categories"
      />
      <SettingsLinkCard
        title="API tokens"
        description="Personal access tokens for KiCad, scripts and agents. Each one acts as the person who created it and can never exceed their role."
        to="/settings/api-tokens"
        action="Manage API tokens"
      />
      <SettingsLinkCard
        title="KiCad setup"
        description="Connect KiCad to this workspace: the HTTP library file, the add-on repository that installs the symbol and footprint files, and the SPICE path variable."
        to="/settings/kicad"
        action="Set up KiCad"
      />
      <SettingsLinkCard
        title="Label templates"
        description="Design the labels the cab SQUIX printer produces for parts, lots, bins, orders and builds. Each type has one default, which is what the Print label action uses."
        to="/settings/label-templates"
        action="Open label designer"
      />

      {cur && <ProvidersCard workspace={cur} workspaceId={workspaceId} />}
      {cur && <SourcingCard workspace={cur} workspaceId={workspaceId} />}
      {cur && <ActiveListsCard workspace={cur} workspaceId={workspaceId} />}

      {cur && (
        <div className="card p-4 mb-4 space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <h2 className="card-title">Scanner</h2>
            <select
              className="input max-w-[220px]"
              aria-label="Barcode scanner"
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
                  aria-label="Scandit license key"
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

      <MembersCard members={members} refetch={refetchMembers} onError={setErr} />
      <InvitationsCard invitations={invites} refetch={refetchInvites} onError={setErr} />

      <div className="card p-4 mb-4 space-y-3 text-sm">
        <h2 className="card-title">All workspaces</h2>
        <ul className="space-y-1">
          {me?.workspaces.map(w => (
            <li key={w.id} className="card p-3 flex items-center justify-between">
              <span>{w.name} <span className="pill ml-2">{w.kind}</span></span>
              <span className="font-mono text-xs text-muted">{w.id}</span>
            </li>
          ))}
        </ul>
        <div className="flex gap-2 items-end pt-1">
          <div className="flex-1">
            <label className="label" htmlFor="new-workspace-name">Create new workspace</label>
            <input
              id="new-workspace-name"
              className="input max-w-xs"
              placeholder="Workspace name"
              value={newName}
              onChange={e => setNewName(e.target.value)}
            />
          </div>
          <button className="btn-primary" onClick={createWs}>Create</button>
        </div>
      </div>
    </div>
  );
}
