import { useState } from "react";
import { useOutletContext } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ExternalLink, RefreshCw, Unlink } from "lucide-react";
import { DataTable, type Column } from "@/components/DataTable";
import { useConfirm } from "@/components/ConfirmDialog";
import { api, ApiError } from "@/lib/api";
import {
  isCatalogKey,
  providerNamespaceOf,
  stripProviderNamespace,
} from "@/lib/providerCatalog";
import { providerLabel as labelFor, providerSearchUrl } from "@/lib/providers";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { isSafeHttpUrl } from "@/lib/url";
import type { CustomFieldRow, Part, ProviderLink } from "@/types";

/** Sentinel for "the primary refresh is running" in the busy state. */
const PRIMARY = "__primary__";

/**
 * Sourcing tab — provider-side catalog data (in-stock count, unit-price
 * tiers, lead time, lifecycle, RoHS / REACH, packaging, distributor
 * P/Ns, etc.) split out of the parametric Specs tab so the latter
 * stays focused on technical values.
 *
 * Rows live in the same `custom_fields` table as Specs — the split is
 * purely client-side via `isCatalogKey()`. The PRIMARY provider's rows
 * have bare keys and fill the table at the top; each SECONDARY provider
 * writes under a `"{provider}:"` prefix and gets its own section below,
 * with its own Refresh (`?provider=`) and Unlink.
 */
export default function PartSourcing() {
  const { part } = useOutletContext<{ part: Part }>();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const [busy, setBusy] = useState<string | null>(null);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: useWsKey("part", part.id, "custom-fields"),
    queryFn: ({ signal }) =>
      api.get<CustomFieldRow[]>(`/custom-fields/by-object/part/${part.id}`, { signal }),
  });

  const allRows = data ?? [];
  // The primary owns every catalog key that isn't in a provider namespace.
  const rows = allRows.filter(r => isCatalogKey(r.key) && !providerNamespaceOf(r.key));
  const secondaryLinks = (part.provider_links ?? []).filter(
    link => link.provider !== part.linked_provider,
  );

  function invalidate() {
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", part.id, "custom-fields") });
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", part.id) });
  }

  async function refresh(provider?: string) {
    setBusy(provider ?? PRIMARY);
    try {
      const query = provider ? `?provider=${encodeURIComponent(provider)}` : "";
      await api.post(`/parts/${part.id}/refresh-from-provider${query}`);
      invalidate();
      toast.success(
        provider ? `Refreshed from ${labelFor(provider)}.` : "Refreshed from provider.",
      );
    } catch (e) {
      toast.error(e instanceof ApiError ? e.userMessage : "Refresh failed");
    } finally {
      setBusy(null);
    }
  }

  async function unlink(provider: string) {
    setBusy(provider);
    try {
      await api.delete(`/parts/${part.id}/provider-links/${encodeURIComponent(provider)}`);
      invalidate();
      toast.success(`${labelFor(provider)} unlinked.`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.userMessage : "Unlink failed");
    } finally {
      setBusy(null);
    }
  }

  const providerLabel = part.linked_provider ? labelFor(part.linked_provider) : null;
  const externalUrl = providerSearchUrl(part.linked_provider, part.mpn);
  const safeExternalUrl = isSafeHttpUrl(externalUrl) ? externalUrl : null;
  const columns: Column<CustomFieldRow>[] = [
    {
      key: "key",
      header: "Field",
      accessor: row => stripProviderNamespace(row.key),
      render: row => (
        <span className="text-muted whitespace-nowrap">
          {stripProviderNamespace(row.key)}
        </span>
      ),
    },
    {
      key: "value",
      header: "Value",
      accessor: row => row.value,
      render: row => <span className="tabular-nums">{row.value}</span>,
    },
  ];

  return (
    <div className="space-y-4">
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
              disabled={busy !== null}
              onClick={() => refresh()}
            >
              <RefreshCw size={14} className={busy === PRIMARY ? "animate-spin" : ""} />
              {busy === PRIMARY ? "Refreshing…" : "Refresh"}
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

      {secondaryLinks.map(link => (
        <SecondaryProviderCard
          key={link.provider}
          link={link}
          rows={allRows.filter(r => providerNamespaceOf(r.key) === link.provider)}
          columns={columns}
          busy={busy}
          onRefresh={() => refresh(link.provider)}
          onUnlink={() => unlink(link.provider)}
        />
      ))}
    </div>
  );
}

interface SecondaryProviderCardProps {
  link: ProviderLink;
  rows: CustomFieldRow[];
  columns: Column<CustomFieldRow>[];
  busy: string | null;
  onRefresh: () => void;
  onUnlink: () => void;
}

/**
 * One secondary provider's catalog data. Its fields never touch the
 * part's own manufacturer / description — this is a second opinion on
 * price and availability, not a second source of truth.
 */
function SecondaryProviderCard({
  link,
  rows,
  columns,
  busy,
  onRefresh,
  onUnlink,
}: SecondaryProviderCardProps) {
  // Held here rather than in PartSourcing so the tab itself stays
  // renderable without a ConfirmDialogProvider — this card is the only
  // part of it that can destroy anything.
  const confirm = useConfirm();
  const label = labelFor(link.provider);
  const safeSourceUrl = isSafeHttpUrl(link.source_url) ? link.source_url : null;
  const isBusy = busy === link.provider;

  async function confirmThenUnlink() {
    const ok = await confirm({
      message: `Unlink ${label} from this part? Its catalog fields will be removed.`,
      severity: "danger",
      confirmLabel: "Unlink",
    });
    if (ok) onUnlink();
  }

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-md font-semibold flex items-center gap-2">
            {label}
            <span className="pill">Additional provider</span>
          </h3>
          <div className="text-xs text-muted">
            {link.external_id && (
              <span className="font-mono">{link.external_id}</span>
            )}
            {link.last_refresh_at && (
              <span className="ml-2">· refreshed {formatDateTime(link.last_refresh_at)}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {safeSourceUrl && (
            <a
              href={safeSourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="btn inline-flex items-center gap-1.5 text-sm"
            >
              <ExternalLink size={14} />
              Open at {label}
            </a>
          )}
          <button
            type="button"
            className="btn inline-flex items-center gap-1.5"
            aria-label={`Refresh ${label}`}
            disabled={busy !== null}
            onClick={onRefresh}
          >
            <RefreshCw size={14} className={isBusy ? "animate-spin" : ""} />
            Refresh
          </button>
          <button
            type="button"
            className="btn-danger inline-flex items-center gap-1.5"
            aria-label={`Unlink ${label}`}
            disabled={busy !== null}
            onClick={confirmThenUnlink}
          >
            <Unlink size={14} />
            Unlink
          </button>
        </div>
      </div>
      {rows.length === 0 ? (
        <div className="text-sm text-muted py-4 text-center">
          No catalog data from {label} yet.
        </div>
      ) : (
        <DataTable
          rows={rows}
          columns={columns}
          rowKey={row => row.id}
          tableId={`part-sourcing-${link.provider}`}
          exportFilename={`part-sourcing-${link.provider}`}
          searchPlaceholder={`Search ${label} data...`}
        />
      )}
    </div>
  );
}
