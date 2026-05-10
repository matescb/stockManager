import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BellPlus, ExternalLink, Plus, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { InlineQueryError } from "@/components/QueryStateBoundary";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { bestUnitPriceAtQty, extendedPrice, type SourcingPriceBreak } from "@/lib/sourcing";
import type { Column } from "@/components/DataTable";
import { DataTable } from "@/components/DataTable";
import { PoweredByTrustedParts } from "@/components/PoweredByTrustedParts";
import { SourcingSourceLabel } from "@/components/SourcingSourceLabel";
import type { SourcingAlertType } from "@/lib/sourcingAlerts";
import AlertFormModal from "@/routes/sourcing/alerts/AlertFormModal";
import {
  CreateOrderLineModal,
  type CreateOrderLineSource,
} from "./CreateOrderLineModal";

type SourcingReason = "ok" | "no_mpn";
type FxStatus = "unavailable";
type WirePrice = number | string;

type SourcingWorkspaceSettings = {
  sourcing_currency_code?: string | null;
};

type SourcingPriceBreakWire = {
  quantity: number;
  unit_price: WirePrice;
};

type SourcingDistributor = {
  name: string;
  sku?: string | null;
  packaging?: string | null;
  moq?: number | null;
  lead_time_days?: number | null;
  stock?: number | null;
  unit_price?: WirePrice | null;
  currency?: string | null;
  unit_price_converted?: WirePrice | null;
  currency_displayed?: string | null;
  fx_converted?: boolean | null;
  fx_rate_date?: string | null;
  price_breaks?: SourcingPriceBreakWire[] | null;
  price_breaks_converted?: SourcingPriceBreakWire[] | null;
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
  fx_status?: FxStatus | null;
};

type SupplyRow = {
  id: string;
  distributor: string;
  stock: number | null;
  moq: number | null;
  packaging: string | null;
  unitPrice: number | null;
  currency: string | null;
  priceBreaks: SourcingPriceBreak[];
  leadTimeDays: number | null;
  link: string | null;
  originalUnitPrice: number | null;
  originalCurrency: string | null;
  fxConverted: boolean;
  fxRateDate: string | null;
};

const QUANTITY_PRESETS = [1, 10, 100, 1000] as const;

function formatNumber(value: number | null): string {
  return value == null ? "—" : value.toLocaleString();
}

function formatPrice(row: SupplyRow): string {
  if (row.unitPrice == null) return "—";
  return formatPriceValue(row.unitPrice, row.currency);
}

function formatPriceValue(value: number, currency: string | null): string {
  const price = value.toLocaleString(undefined, {
    maximumFractionDigits: 6,
    minimumFractionDigits: 0,
  });
  return currency ? `${price} ${currency}` : price;
}

function toFiniteNumber(value: WirePrice | null | undefined): number | null {
  if (value == null) return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalisePriceBreaks(value: SourcingPriceBreakWire[] | null | undefined): SourcingPriceBreak[] {
  return (value ?? []).flatMap(priceBreak => {
    const unitPrice = toFiniteNumber(priceBreak.unit_price);
    return unitPrice == null ? [] : [{ quantity: priceBreak.quantity, unit_price: unitPrice }];
  });
}

function fxTooltip(row: SupplyRow): string {
  const original = row.originalUnitPrice == null
    ? row.originalCurrency ?? "native currency"
    : formatPriceValue(row.originalUnitPrice, row.originalCurrency);
  return `Converted from ${original} via ECB daily rate (${row.fxRateDate ?? "unknown date"})`;
}

function formatLeadTime(days: number | null): string {
  if (days == null) return "—";
  return days === 1 ? "1 day" : `${days.toLocaleString()} days`;
}

function normaliseQuantity(value: string): number | null {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 1) return null;
  return Math.floor(parsed);
}

function flattenOffers(data: SourcingResponse | undefined): SupplyRow[] {
  if (!data || data.reason !== "ok") return [];

  return data.offers.flatMap((offer, offerIndex) =>
    offer.distributors.map((distributor, distributorIndex) => {
      const originalUnitPrice = toFiniteNumber(distributor.unit_price);
      const convertedUnitPrice = toFiniteNumber(distributor.unit_price_converted);
      const fxConverted = distributor.fx_converted === true && convertedUnitPrice !== null;
      return {
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
        unitPrice: fxConverted ? convertedUnitPrice : originalUnitPrice,
        currency: fxConverted
          ? distributor.currency_displayed ?? distributor.currency ?? null
          : distributor.currency ?? null,
        priceBreaks: fxConverted
          ? normalisePriceBreaks(distributor.price_breaks_converted)
          : normalisePriceBreaks(distributor.price_breaks),
        leadTimeDays: distributor.lead_time_days ?? null,
        link: distributor.product_url ?? offer.links?.primary ?? data.links?.primary ?? null,
        originalUnitPrice,
        originalCurrency: distributor.currency ?? null,
        fxConverted,
        fxRateDate: distributor.fx_rate_date ?? null,
      };
    }),
  );
}

function PriceCell({ row }: { row: SupplyRow }) {
  return (
    <span className="inline-flex items-center justify-end gap-1.5">
      <span>{formatPrice(row)}</span>
      {row.fxConverted && (
        <span
          className="inline-flex h-5 w-5 items-center justify-center rounded border border-accent/40 text-accent"
          title={fxTooltip(row)}
          aria-label={fxTooltip(row)}
        >
          <RefreshCw size={12} aria-hidden="true" />
        </span>
      )}
    </span>
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
    <div className="card p-4 space-y-3" role="status">
      <PoweredByTrustedParts primaryUrl={primaryUrl ?? undefined} />
      <div className="text-sm text-muted">{children}</div>
    </div>
  );
}

function errorStatus(error: unknown): number | null {
  return error instanceof ApiError ? error.status : null;
}

function retryAfterSeconds(error: unknown): number | null {
  if (!(error instanceof ApiError) || error.status !== 429) return null;
  const retryAfter = (error.body as { retry_after_seconds?: unknown } | null)
    ?.retry_after_seconds;
  return typeof retryAfter === "number" && Number.isFinite(retryAfter)
    ? Math.max(1, Math.ceil(retryAfter))
    : 60;
}

function rateLimitMessage(seconds: number): string {
  return `TrustedParts refresh rate limit reached — try again in ${seconds} ${
    seconds === 1 ? "second" : "seconds"
  }.`;
}

export function AuthorizedSupplyTab({ partId }: { partId: string }) {
  const queryClient = useQueryClient();
  const { workspaceId } = useAuth();
  const workspaceQuery = useQuery({
    queryKey: useWsKey("ws", "current"),
    queryFn: () => api.get<SourcingWorkspaceSettings>("/workspaces/current"),
  });
  const workspaceCurrency = workspaceQuery.data?.sourcing_currency_code?.trim().toUpperCase() || null;
  const sourcingPath = workspaceCurrency
    ? `/parts/${partId}/sourcing?currency=${encodeURIComponent(workspaceCurrency)}`
    : `/parts/${partId}/sourcing`;
  const queryKey = useWsKey("part", partId, "sourcing", workspaceCurrency ?? "native");
  const invalidateKey = wsKeyOf(workspaceId, "part", partId, "sourcing", workspaceCurrency ?? "native");
  const [selectedDistributors, setSelectedDistributors] = useState<Set<string>>(() => new Set());
  const [quantity, setQuantity] = useState(1);
  const [customQuantity, setCustomQuantity] = useState("1");
  const [orderLineSource, setOrderLineSource] = useState<CreateOrderLineSource | null>(null);
  const [alertModalOpen, setAlertModalOpen] = useState(false);

  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) =>
      api.get<SourcingResponse>(sourcingPath, { signal }),
    enabled: workspaceQuery.isSuccess,
  });

  const refreshMutation = useApiMutation<SourcingResponse, void>({
    mutationKey: wsKeyOf(workspaceId, "part", partId, "sourcing-refresh"),
    mutationFn: () =>
      api.post<SourcingResponse, Record<string, never>>(`/parts/${partId}/sourcing/refresh`, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: invalidateKey });
    },
    onError: error => {
      const status = errorStatus(error);
      if (status === 429 || status === 503) return;
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

  const columns = useMemo<Column<SupplyRow>[]>(() => {
    const quantityColumns: Column<SupplyRow>[] = quantity > 1
      ? [
          {
            key: "unitPriceAtQty",
            header: `Unit price @ ${quantity.toLocaleString()}`,
            accessor: row => bestUnitPriceAtQty(row.priceBreaks, quantity)?.unitPrice,
            render: row => {
              const best = bestUnitPriceAtQty(row.priceBreaks, quantity);
              return best === null ? (
                <span className="text-muted">Below MOQ</span>
              ) : (
                formatPriceValue(best.unitPrice, row.currency)
              );
            },
            align: "right",
          },
          {
            key: "extendedAtQty",
            header: `Extended @ ${quantity.toLocaleString()}`,
            accessor: row => extendedPrice(row.priceBreaks, quantity),
            render: row => {
              const extended = extendedPrice(row.priceBreaks, quantity);
              return extended === null ? (
                <span className="text-muted">—</span>
              ) : (
                formatPriceValue(extended, row.currency)
              );
            },
            align: "right",
          },
        ]
      : [];

    const baseColumns: Column<SupplyRow>[] = [
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
        render: row => <PriceCell row={row} />,
        align: "right",
      },
      ...quantityColumns,
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
      {
        key: "order",
        header: "",
        render: row => (
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              const best = bestUnitPriceAtQty(row.priceBreaks, quantity);
              setOrderLineSource({
                partId,
                distributor: row.distributor,
                packaging: row.packaging,
                leadTimeDays: row.leadTimeDays,
                fetchedAt: query.data?.fetched_at ?? null,
                quantity,
                unitPrice: best?.unitPrice ?? row.unitPrice,
                currency: row.currency,
                productUrl: row.link,
              });
            }}
          >
            <Plus size={14} aria-hidden="true" />
            Add to order
          </button>
        ),
      },
    ];
    return baseColumns;
  }, [partId, quantity, query.data?.fetched_at]);

  const status = errorStatus(query.error);
  const refreshStatus = errorStatus(refreshMutation.error);
  const retryAfter = retryAfterSeconds(refreshMutation.error) ?? retryAfterSeconds(query.error);
  const primaryUrl = query.data?.links?.primary ?? undefined;

  if (workspaceQuery.isError) {
    return <InlineQueryError query={workspaceQuery} label="workspace settings" />;
  }

  if (workspaceQuery.isLoading || query.isLoading) {
    return (
      <div className="card p-3 text-sm text-muted" role="status">
        Loading authorized supply…
      </div>
    );
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
        <div className="flex flex-wrap justify-end gap-2">
          <button
            type="button"
            className="btn inline-flex items-center gap-1.5"
            onClick={() => setAlertModalOpen(true)}
          >
            <BellPlus size={14} aria-hidden="true" />
            Set alert
          </button>
          <button
            type="button"
            className="btn-primary inline-flex items-center gap-1.5"
            disabled={refreshMutation.isPending}
            onClick={() => {
              refreshMutation.reset();
              refreshMutation.mutate();
            }}
          >
            <RefreshCw size={14} className={refreshMutation.isPending ? "animate-spin" : ""} />
            {refreshMutation.isPending ? "Refreshing…" : "Refresh live"}
          </button>
        </div>
      </div>

      {status === 503 && (
        <div className="card p-3 text-sm text-muted" role="status">
          TrustedParts request budget reached for this hour — try again later.
        </div>
      )}

      {(status === 429 || refreshStatus === 429) && retryAfter !== null && (
        <div className="card p-3 text-sm text-muted" role="status">
          {rateLimitMessage(retryAfter)}
        </div>
      )}

      {status === 502 && (
        <div className="card p-3 text-sm text-muted" role="status">
          <button type="button" className="btn" onClick={() => refetch()}>
            Retry TrustedParts
          </button>
        </div>
      )}

      {query.isError && status !== 429 && status !== 502 && status !== 503 && status !== 409 && (
        <InlineQueryError query={query} label="authorized supply" />
      )}

      {query.data?.fx_status === "unavailable" && (
        <div className="card p-3 text-sm text-muted" role="status">
          FX conversion unavailable for some rows — showing native currency.
        </div>
      )}

      {distributors.length > 0 && (
        <div className="flex flex-wrap items-end gap-4">
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
          <div className="space-y-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted">
              Quantity
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {QUANTITY_PRESETS.map(preset => (
                <button
                  key={preset}
                  type="button"
                  className={preset === quantity ? "btn-primary btn-sm" : "btn btn-sm"}
                  aria-pressed={preset === quantity}
                  onClick={() => {
                    setQuantity(preset);
                    setCustomQuantity(String(preset));
                  }}
                >
                  {preset.toLocaleString()}
                </button>
              ))}
              <label className="flex items-center gap-2 text-sm text-muted">
                Custom:
                <input
                  className="input h-8 w-24"
                  type="number"
                  min={1}
                  step={1}
                  inputMode="numeric"
                  value={customQuantity}
                  onChange={event => setCustomQuantity(event.currentTarget.value)}
                  onBlur={() => {
                    const nextQuantity = normaliseQuantity(customQuantity);
                    if (nextQuantity === null) {
                      setCustomQuantity(String(quantity));
                      return;
                    }
                    setQuantity(nextQuantity);
                    setCustomQuantity(String(nextQuantity));
                  }}
                  onKeyDown={event => {
                    if (event.key === "Enter") {
                      event.currentTarget.blur();
                    }
                  }}
                />
              </label>
            </div>
          </div>
        </div>
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
      <CreateOrderLineModal
        open={orderLineSource !== null}
        source={orderLineSource}
        onClose={() => setOrderLineSource(null)}
      />
      <AlertFormModal
        open={alertModalOpen}
        title="Set alert on this part"
        initialValues={{ part_id: partId, alert_type: "back_in_stock" }}
        allowedTypes={["back_in_stock", "out_of_authorized_stock", "price_changed", "stock_below", "stock_above"] satisfies SourcingAlertType[]}
        onClose={() => setAlertModalOpen(false)}
        onSaved={() => {
          queryClient.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "sourcing-alerts") });
          toast.success("Alert created.");
        }}
      />
    </div>
  );
}

export default AuthorizedSupplyTab;
