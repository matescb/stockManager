import { useState } from "react";
import { useOutletContext } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ExternalLink, RefreshCw } from "lucide-react";
import { DataTable, type Column } from "@/components/DataTable";
import { api, ApiError } from "@/lib/api";
import { isCatalogKey } from "@/lib/providerCatalog";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { isSafeHttpUrl } from "@/lib/url";
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
  const { workspaceId } = useAuth();
  const [refreshing, setRefreshing] = useState(false);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: useWsKey("part", part.id, "custom-fields"),
    queryFn: () =>
      api.get<CustomFieldRow[]>(`/custom-fields/by-object/part/${part.id}`),
  });

  const rows = (data ?? []).filter(r => isCatalogKey(r.key));

  async function refresh() {
    setRefreshing(true);
    try {
      await api.post(`/parts/${part.id}/refresh-from-provider`);
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", part.id, "custom-fields") });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", part.id) });
      toast.success("Refreshed from provider.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.userMessage : "Refresh failed");
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
  const safeExternalUrl = isSafeHttpUrl(externalUrl) ? externalUrl : null;
  const columns: Column<CustomFieldRow>[] = [
    {
      key: "key",
      header: "Field",
      accessor: row => row.key,
      render: row => <span className="text-muted whitespace-nowrap">{row.key}</span>,
    },
    {
      key: "value",
      header: "Value",
      accessor: row => row.value,
      render: row => <span className="tabular-nums">{row.value}</span>,
    },
  ];

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
                    · refreshed {formatDateTime(part.last_refresh_at)}
                  </span>
                )}
              </>
            ) : (
              "No provider linked."
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {safeExternalUrl && (
            <a
              href={safeExternalUrl}
              target="_blank"
              rel="noopener noreferrer"
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
      {isError ? (
        <div className="text-red-600 text-sm">Failed to load sourcing data. {error instanceof ApiError ? error.userMessage : ""}</div>
      ) : isLoading ? (
        <div className="text-muted text-sm">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="text-sm text-muted py-4 text-center">
          No catalog data yet. Click Refresh to pull stock + pricing from {providerLabel ?? "the provider"}.
        </div>
      ) : (
        <DataTable
          rows={rows}
          columns={columns}
          rowKey={row => row.id}
          tableId="part-sourcing-catalog"
          exportFilename="part-sourcing"
          searchPlaceholder="Search catalog data..."
        />
      )}
    </div>
  );
}
