import { useState } from "react";
import { useOutletContext } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ExternalLink, FileText, Loader2, RefreshCw } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import { InlineQueryError } from "@/components/QueryStateBoundary";
import { isSafeHttpOrSameOriginUrl } from "@/lib/url";
import type { CustomFieldRow, Part } from "@/types";
import { providerLabel } from "@/lib/providers";

const STALE_DAYS = 30;

/**
 * Append `?name=<safe>` to an asset URL so the backend serves it with
 * `Content-Disposition: inline; filename="<safe>.<ext>"`. The browser
 * still previews PDFs / images inline; the user's Save As dialog
 * shows the readable filename instead of the content-hash.
 *
 * Only applies to our own asset paths; remote URLs (still possible
 * when a download fell back to the upstream link) get returned as-is.
 */
function withDownloadName(url: string, name: string | null | undefined): string {
  if (!url || !url.startsWith("/api/parts/assets/")) return url;
  const safe = (name || "datasheet").replace(/[^a-zA-Z0-9._-]+/g, "_").slice(0, 80);
  if (!safe) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}name=${encodeURIComponent(safe)}`;
}

function relativeTime(iso: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return "just now";
  const min = Math.round(ms / 60_000);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 48) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
}

function isStale(iso: string | null): boolean {
  if (!iso) return false;
  return Date.now() - new Date(iso).getTime() > STALE_DAYS * 24 * 3600 * 1000;
}

export default function PartInfo() {
  const { part } = useOutletContext<{ part: Part }>();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const cfQuery = useQuery({
    queryKey: useWsKey("part", part.id, "custom-fields"),
    queryFn: ({ signal }) =>
      api.get<CustomFieldRow[]>(`/custom-fields/by-object/part/${part.id}`, { signal }),
  });
  const { data: cf } = cfQuery;
  const lookupBy = (k: string) => cf?.find(r => r.key === k)?.value || null;
  // Image now lives in the layout header (passed in via Part.image_url);
  // the only Media-card affordance that still belongs on this page is the
  // datasheet link.
  const datasheetUrl = lookupBy("datasheet_url");
  const safeDatasheetUrl = isSafeHttpOrSameOriginUrl(datasheetUrl) ? datasheetUrl : null;

  const [refreshing, setRefreshing] = useState(false);
  async function refresh() {
    setRefreshing(true);
    try {
      const r = await api.post<{
        found: boolean;
        provider?: string;
        message?: string;
        summary?: { added: number; updated: number; removed: number };
      }>(`/parts/${part.id}/refresh-from-provider`, {});
      if (r.found && r.summary) {
        const { added, updated, removed } = r.summary;
        toast.success(
          `Refreshed from ${providerLabel(r.provider)}: ` +
            `${added} added, ${updated} updated, ${removed} removed.`,
        );
      } else {
        toast.message(r.message || "No upstream match.");
      }
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", part.id) });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", part.id, "custom-fields") });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.userMessage : "Refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  const linked = !!part.linked_provider;
  const linkedProviderName = linked ? providerLabel(part.linked_provider) : null;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      {cfQuery.isError && (
        <div className="col-span-2">
          <InlineQueryError query={cfQuery} label="custom fields" />
        </div>
      )}
      {linked && (
        <div className="card p-3 col-span-2 flex items-center gap-3 text-sm">
          <RefreshCw size={14} className="text-accent shrink-0" />
          <div className="flex-1">
            <span className="font-medium">Linked to {linkedProviderName}</span>
            <span className="text-muted ml-2">
              · last refreshed {relativeTime(part.last_refresh_at)}
              {isStale(part.last_refresh_at) && (
                <span className="ml-2 pill bg-warning/20 text-warning">stale</span>
              )}
            </span>
            {part.linked_external_id && (
              <span className="ml-2 text-xs text-muted font-mono">
                {part.linked_external_id}
              </span>
            )}
          </div>
          <button
            type="button"
            className="btn"
            onClick={refresh}
            disabled={refreshing}
            title={`Re-pull ${linkedProviderName} data`}
          >
            {refreshing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Refresh
          </button>
        </div>
      )}
      <div className="card p-4">
        <h3 className="text-sm uppercase tracking-wider text-muted mb-2">Identity</h3>
        <Field label="Name" value={part.name} />
        <Field
          label="Manufacturer"
          value={part.manufacturer}
          badge={linked ? <ProviderBadge label={linkedProviderName!} /> : null}
        />
        <Field
          label="MPN"
          value={part.mpn}
          badge={linked ? <ProviderBadge label={linkedProviderName!} /> : null}
        />
        <Field label="Internal P/N" value={part.internal_part_number} />
        <Field label="Footprint" value={part.footprint} />
      </div>
      <div className="card p-4">
        <h3 className="text-sm uppercase tracking-wider text-muted mb-2">Stock</h3>
        <Field label="On hand" value={String(part.on_hand ?? 0)} />
        <Field label="Low-stock threshold" value={part.low_stock_report_quantity != null ? String(part.low_stock_report_quantity) : null} />
        <Field label="Attrition" value={`${part.attrition_percentage}% (min ${part.attrition_min_quantity})`} />
      </div>
      {safeDatasheetUrl && (
        <div className="card p-4 col-span-2">
          <a
            href={withDownloadName(safeDatasheetUrl, part.mpn || part.name)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-accent hover:underline text-sm"
          >
            <FileText size={14} /> Datasheet <ExternalLink size={12} />
          </a>
        </div>
      )}
      <div className="card p-4 col-span-2">
        <div className="flex items-center mb-2">
          <h3 className="text-sm uppercase tracking-wider text-muted">Description</h3>
          {linked && (
            part.description_locally_edited ? (
              <span className="pill ml-2 bg-warning/20 text-warning">Locally edited</span>
            ) : (
              <ProviderBadge label={linkedProviderName!} className="ml-2" />
            )
          )}
        </div>
        <p className="whitespace-pre-wrap text-sm">{part.description || <span className="text-muted">—</span>}</p>
      </div>
    </div>
  );
}

function ProviderBadge({ label, className }: { label: string; className?: string }) {
  return (
    <span className={`pill bg-accent/15 text-accent ${className ?? ""}`}>{label}</span>
  );
}

function Field({
  label,
  value,
  badge,
}: {
  label: string;
  value: string | null | undefined;
  badge?: React.ReactNode;
}) {
  return (
    <div className="text-sm py-1 flex items-baseline gap-2">
      <span className="text-muted w-40 inline-block">{label}</span>
      <span>{value || <span className="text-muted">—</span>}</span>
      {badge && <span className="ml-auto">{badge}</span>}
    </div>
  );
}
