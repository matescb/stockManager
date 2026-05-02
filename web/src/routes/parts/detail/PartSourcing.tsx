import { useState } from "react";
import { useOutletContext } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ExternalLink, RefreshCw } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { isCatalogKey } from "@/lib/providerCatalog";
import { wsKey } from "@/lib/queryKeys";
import type { CustomFieldRow, Part } from "@/types";

const PROVIDER_LABEL: Record<string, string> = {
  mouser: "Mouser",
  digikey: "DigiKey",
};

const PROVIDER_SEARCH_URL: Record<string, (mpn: string) => string> = {
  mouser: mpn => `https://www.mouser.com/c/?q=${encodeURIComponent(mpn)}`,
  digikey: mpn => `https://www.digikey.com/en/products/result?keywords=${encodeURIComponent(mpn)}`,
};

/**
 * Sourcing tab — provider-side catalog data (in-stock count, unit-price
 * tiers, lead time, lifecycle, RoHS / REACH, packaging, distributor
 * P/Ns, etc.) split out of the parametric Specs tab so the latter
 * stays focused on technical values.
 *
 * Rows live in the same `custom_fields` table as Specs — the split is
 * purely client-side via `isCatalogKey()`. A "Refresh" button calls
 * the existing /parts/{id}/refresh-from-provider endpoint.
 */
export default function PartSourcing() {
  const { part } = useOutletContext<{ part: Part }>();
  const qc = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: wsKey("part", part.id, "custom-fields"),
    queryFn: () =>
      api.get<CustomFieldRow[]>(`/custom-fields/by-object/part/${part.id}`),
  });

  const rows = (data ?? []).filter(r => isCatalogKey(r.key));

  async function refresh() {
    setRefreshing(true);
    try {
      await api.post(`/parts/${part.id}/refresh-from-provider`);
      qc.invalidateQueries({ queryKey: wsKey("part", part.id, "custom-fields") });
      qc.invalidateQueries({ queryKey: wsKey("part", part.id) });
      toast.success("Refreshed from provider.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  const providerLabel = part.linked_provider
    ? PROVIDER_LABEL[part.linked_provider] ?? part.linked_provider
    : null;
  const externalUrl =
    part.linked_provider && part.mpn
      ? PROVIDER_SEARCH_URL[part.linked_provider]?.(part.mpn)
      : null;

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-md font-semibold">Sourcing</h3>
          <div className="text-xs text-muted">
            {providerLabel ? (
              <>
                Catalog data from <strong className="text-text">{providerLabel}</strong>
                {part.last_refresh_at && (
                  <span className="ml-2">
                    · refreshed {new Date(part.last_refresh_at).toLocaleString()}
                  </span>
                )}
              </>
            ) : (
              "No provider linked."
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {externalUrl && (
            <a
              href={externalUrl}
              target="_blank"
              rel="noreferrer"
              className="btn inline-flex items-center gap-1.5 text-sm"
            >
              <ExternalLink size={14} />
              Open at {providerLabel}
            </a>
          )}
          <button
            type="button"
            className="btn-primary inline-flex items-center gap-1.5"
            disabled={refreshing}
            onClick={refresh}
          >
            <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>
      {isLoading ? (
        <div className="text-muted text-sm">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="text-sm text-muted py-4 text-center">
          No catalog data yet. Click Refresh to pull stock + pricing from {providerLabel ?? "the provider"}.
        </div>
      ) : (
        <table className="text-sm w-full">
          <tbody>
            {rows.map(r => (
              <tr key={r.id} className="align-top">
                <td className="text-muted pr-4 py-1 whitespace-nowrap">{r.key}</td>
                <td className="py-1 tabular-nums">{r.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
