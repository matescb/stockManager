import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { ApiError, api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Column } from "@/components/DataTable";
import { DataTable } from "@/components/DataTable";
import { PoweredByTrustedParts } from "@/components/PoweredByTrustedParts";
import { SourcingSourceLabel } from "@/components/SourcingSourceLabel";

type SourcingReason = "ok" | "no_mpn";

type SourcingDistributor = {
  name: string;
  sku?: string | null;
  packaging?: string | null;
  moq?: number | null;
  lead_time_days?: number | null;
  stock?: number | null;
  unit_price?: number | null;
  currency?: string | null;
  product_url?: string | null;
};

type SourcingOffer = {
  mpn: string;
  manufacturer?: string | null;
  description?: string | null;
  distributors: SourcingDistributor[];
  links?: {
    primary?: string | null;
    manufacturer?: string | null;
    datasheet?: string | null;
  };
};

type SourcingResponse = {
  mpn?: string | null;
  offers: SourcingOffer[];
  request_id?: string | null;
  powered_by?: "TrustedParts";
  fetched_at?: string | null;
  cache_hit?: boolean | null;
  links?: {
    primary?: string | null;
    attribution?: string | null;
  };
  reason: SourcingReason;
};

type SupplyRow = {
  id: string;
  distributor: string;
  stock: number | null;
  moq: number | null;
  packaging: string | null;
  unitPrice: number | null;
  currency: string | null;
  leadTimeDays: number | null;
  link: string | null;
};

export const authorizedSupplyQueryKey = (partId: string) => ["sourcing", "part", partId] as const;

function formatNumber(value: number | null): string {
  return value == null ? "—" : value.toLocaleString();
}

function formatPrice(row: SupplyRow): string {
  if (row.unitPrice == null) return "—";
  const price = row.unitPrice.toLocaleString(undefined, {
    maximumFractionDigits: 6,
    minimumFractionDigits: 0,
  });
  return row.currency ? `${price} ${row.currency}` : price;
}

function formatLeadTime(days: number | null): string {
  if (days == null) return "—";
  return days === 1 ? "1 day" : `${days.toLocaleString()} days`;
}

function flattenOffers(data: SourcingResponse | undefined): SupplyRow[] {
  if (!data || data.reason !== "ok") return [];

  return data.offers.flatMap((offer, offerIndex) =>
    offer.distributors.map((distributor, distributorIndex) => ({
      id: [
        offer.mpn,
        distributor.name,
        distributor.sku ?? "sku",
        offerIndex,
        distributorIndex,
      ].join(":"),
      distributor: distributor.name,
      stock: distributor.stock ?? null,
      moq: distributor.moq ?? null,
      packaging: distributor.packaging ?? null,
      unitPrice: distributor.unit_price ?? null,
      currency: distributor.currency ?? null,
      leadTimeDays: distributor.lead_time_days ?? null,
      link: offer.links?.primary ?? data.links?.primary ?? null,
    })),
  );
}

function EmptyState({
  children,
  primaryUrl,
}: {
  children: React.ReactNode;
  primaryUrl?: string | null;
}) {
  return (
    <div className="card p-4 space-y-3">
      <PoweredByTrustedParts primaryUrl={primaryUrl ?? undefined} />
      <div className="text-sm text-muted">{children}</div>
    </div>
  );
}

function errorStatus(error: unknown): number | null {
  return error instanceof ApiError ? error.status : null;
}

export function AuthorizedSupplyTab({ partId }: { partId: string }) {
  const queryClient = useQueryClient();
  const [selectedDistributors, setSelectedDistributors] = useState<Set<string>>(() => new Set());

  const query = useQuery({
    queryKey: authorizedSupplyQueryKey(partId),
    queryFn: ({ signal }) =>
      api.get<SourcingResponse>(`/parts/${partId}/sourcing`, { signal }),
  });

  const refreshMutation = useMutation({
    mutationFn: () => api.post<SourcingResponse, Record<string, never>>(`/parts/${partId}/sourcing/refresh`, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: authorizedSupplyQueryKey(partId) });
    },
    onError: error => {
      if (errorStatus(error) === 503) return;
      toast.error(error instanceof ApiError ? error.message : "Refresh failed");
    },
  });

  const rows = useMemo(() => flattenOffers(query.data), [query.data]);
  const refetch = query.refetch;
  const distributors = useMemo(
    () => Array.from(new Set(rows.map(row => row.distributor))).sort((a, b) => a.localeCompare(b)),
    [rows],
  );
  const filteredRows = useMemo(() => {
    if (selectedDistributors.size === 0) return rows;
    return rows.filter(row => selectedDistributors.has(row.distributor));
  }, [rows, selectedDistributors]);

  useEffect(() => {
    setSelectedDistributors(prev => {
      if (prev.size === 0) return prev;
      const available = new Set(distributors);
      const next = new Set(Array.from(prev).filter(name => available.has(name)));
      return next.size === prev.size ? prev : next;
    });
  }, [distributors]);

  useEffect(() => {
    if (!query.isError || errorStatus(query.error) !== 502) return;
    toast.error("TrustedParts unavailable. Retry?", {
      action: {
        label: "Retry",
        onClick: () => refetch(),
      },
    });
  }, [query.isError, query.error, refetch]);

  const columns = useMemo<Column<SupplyRow>[]>(
    () => [
      {
        key: "distributor",
        header: "Distributor",
        accessor: row => row.distributor,
      },
      {
        key: "stock",
        header: "Stock",
        accessor: row => row.stock,
        render: row => formatNumber(row.stock),
        align: "right",
      },
      {
        key: "moq",
        header: "MOQ",
        accessor: row => row.moq,
        render: row => formatNumber(row.moq),
        align: "right",
      },
      {
        key: "packaging",
        header: "Packaging",
        accessor: row => row.packaging,
        render: row => row.packaging ?? "—",
      },
      {
        key: "price",
        header: "Price",
        accessor: row => row.unitPrice,
        render: row => formatPrice(row),
        align: "right",
      },
      {
        key: "leadTime",
        header: "Lead time",
        accessor: row => row.leadTimeDays,
        render: row => formatLeadTime(row.leadTimeDays),
        align: "right",
      },
      {
        key: "link",
        header: "Link",
        accessor: row => row.link,
        render: row =>
          row.link ? (
            <a
              href={row.link}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-accent hover:underline"
            >
              Open
              <ExternalLink size={14} aria-hidden="true" />
            </a>
          ) : (
            <span className="text-muted">—</span>
          ),
      },
    ],
    [],
  );

  const status = errorStatus(query.error);
  const primaryUrl = query.data?.links?.primary ?? undefined;

  if (query.isLoading) {
    return <div className="text-muted">Loading…</div>;
  }

  if (query.data?.reason === "no_mpn") {
    return (
      <EmptyState primaryUrl={primaryUrl}>
        Add an MPN to this part to see authorized-distributor offers.
      </EmptyState>
    );
  }

  if (status === 409) {
    return (
      <EmptyState>
        Sourcing not configured. Ask a workspace admin to set TrustedParts credentials in Settings → Sourcing.
      </EmptyState>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <PoweredByTrustedParts primaryUrl={primaryUrl} />
          <SourcingSourceLabel source="trustedparts" />
          {query.data?.fetched_at && (
            <span className="text-xs text-muted">
              Last fetched {formatDateTime(query.data.fetched_at)}
            </span>
          )}
        </div>
        <button
          type="button"
          className="btn-primary inline-flex items-center gap-1.5"
          disabled={refreshMutation.isPending}
          onClick={() => refreshMutation.mutate()}
        >
          <RefreshCw size={14} className={refreshMutation.isPending ? "animate-spin" : ""} />
          {refreshMutation.isPending ? "Refreshing…" : "Refresh live"}
        </button>
      </div>

      {status === 503 && (
        <div className="card p-3 text-sm text-muted" role="status">
          TrustedParts request budget reached for this hour — try again later.
        </div>
      )}

      {status === 502 && (
        <div className="card p-3 text-sm text-muted">
          <button type="button" className="btn" onClick={() => refetch()}>
            Retry TrustedParts
          </button>
        </div>
      )}

      {query.isError && status !== 502 && status !== 503 && status !== 409 && (
        <div className="text-red-600 text-sm">
          Failed to load authorized supply. {query.error instanceof ApiError ? query.error.userMessage : ""}
        </div>
      )}

      {distributors.length > 0 && (
        <label className="label max-w-sm">
          Distributor filter
          <select
            multiple
            className="input min-h-28"
            value={Array.from(selectedDistributors)}
            onChange={event => {
              const next = new Set(
                Array.from(event.currentTarget.selectedOptions).map(option => option.value),
              );
              setSelectedDistributors(next);
            }}
          >
            {distributors.map(distributor => (
              <option key={distributor} value={distributor}>
                {distributor}
              </option>
            ))}
          </select>
        </label>
      )}

      <DataTable
        tableId={`part-authorized-supply-${partId}`}
        rows={filteredRows}
        rowKey={row => row.id}
        columns={columns}
        searchPlaceholder="Search offers…"
        exportFilename="authorized-supply"
        empty="No authorized-distributor offers."
      />
    </div>
  );
}

export default AuthorizedSupplyTab;
