import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { BarChart3 } from "lucide-react";
import { PoweredByTrustedParts } from "@/components/PoweredByTrustedParts";
import { SourcingSourceLabel } from "@/components/SourcingSourceLabel";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import { api, ApiError } from "@/lib/api";
import { formatDateTime, formatMoney } from "@/lib/format";
import { useWsKey } from "@/lib/queryKeys";

type SortMode = "delta_pct" | "delta_abs" | "name";

type CurrencyAmount = {
  currency: string | null;
  value: string;
};

type ReplenishmentCostRow = {
  part_id: string;
  name: string;
  manufacturer: string | null;
  mpn: string;
  on_hand: number;
  currency: string | null;
  historical_costs: CurrencyAmount[];
  historical_cost: string | null;
  replacement_unit_price: string | null;
  replacement_cost: string | null;
  replacement_currency: string | null;
  delta_abs: string | null;
  delta_pct: string | null;
  reason: "no_offer" | "currency_mismatch" | "sourcing_not_configured" | "sourcing_unavailable" | null;
  source: "trustedparts" | null;
};

type ReplenishmentCostReport = {
  rows: ReplenishmentCostRow[];
  totals: {
    currency: string | null;
    historical_cost: string;
    replacement_cost: string;
    delta_abs: string | null;
  }[];
  sourcing_status: {
    state: "ok" | "not_configured" | "degraded" | "error";
    message: string | null;
    fetched_at: string | null;
    cache_hit: boolean | null;
    partial: boolean;
    powered_by: "TrustedParts";
    links: { primary: string; attribution: string } | null;
  };
};

const SORT_OPTIONS: { value: SortMode; label: string }[] = [
  { value: "delta_pct", label: "Delta % desc" },
  { value: "delta_abs", label: "Delta desc" },
  { value: "name", label: "Name asc" },
];

function moneyValue(value: string | null, currency: string | null | undefined): string {
  if (value == null) return "-";
  return formatMoney(Number(value), currency);
}

function percentValue(value: string | null): string {
  if (value == null) return "-";
  return `${Number(value).toFixed(2)}%`;
}

function reasonLabel(reason: ReplenishmentCostRow["reason"]): string {
  switch (reason) {
    case "currency_mismatch":
      return "Currency mismatch";
    case "no_offer":
      return "No offer";
    case "sourcing_not_configured":
      return "Not configured";
    case "sourcing_unavailable":
      return "Sourcing unavailable";
    default:
      return "";
  }
}

function historicalSummary(row: ReplenishmentCostRow): string {
  if (row.historical_costs.length === 0) return "-";
  return row.historical_costs
    .map(item => moneyValue(item.value, item.currency))
    .join(" / ");
}

function StatusBanner({ report }: { report: ReplenishmentCostReport }) {
  const status = report.sourcing_status;
  const primaryUrl = status.links?.primary;
  if (status.state === "ok") {
    return (
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <PoweredByTrustedParts primaryUrl={primaryUrl ?? undefined} />
        <span>{status.cache_hit ? "Served from cache" : "Fresh pricing"}</span>
        {status.fetched_at && <span>{formatDateTime(status.fetched_at)}</span>}
      </div>
    );
  }

  const tone = status.state === "not_configured" ? "border-warning/40 text-warning" : "border-danger/40 text-danger";
  return (
    <div className={`border rounded-md px-3 py-2 text-sm flex flex-wrap items-center gap-2 ${tone}`}>
      <PoweredByTrustedParts primaryUrl={primaryUrl ?? undefined} />
      <span>{status.message ?? "TrustedParts sourcing unavailable"}</span>
    </div>
  );
}

export default function ReplenishmentCostReport() {
  const [sort, setSort] = useState<SortMode>("delta_pct");
  const { data, isError, isLoading, error } = useQuery({
    queryKey: useWsKey("report", "replenishment-cost", sort),
    queryFn: ({ signal }) => api.get<ReplenishmentCostReport>(`/reports/replenishment-cost?sort=${sort}`, { signal }),
  });

  if (isError) {
    return (
      <div className="text-red-600 text-sm p-4">
        Failed to load replenishment cost report. {error instanceof ApiError ? error.userMessage : ""}
      </div>
    );
  }
  if (isLoading) return <div className="text-muted">Loading...</div>;
  if (!data) return null;

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <StatusBanner report={data} />
        <div className="w-full sm:w-56">
          <label className="label" htmlFor="report-replenishment-sort">Sort</label>
          <select
            id="report-replenishment-sort"
            className="input"
            value={sort}
            onChange={event => setSort(event.target.value as SortMode)}
          >
            {SORT_OPTIONS.map(option => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>
      </div>

      <DataTable
        rows={data.rows}
        rowKey={row => row.part_id}
        tableId="report-replenishment-cost"
        empty={
          <EmptyState
            icon={BarChart3}
            title="No data"
            description="No on-hand MPN-tagged stock is available for replenishment pricing."
          />
        }
        exportFilename="replenishment-cost"
        columns={[
          {
            key: "name",
            header: "Part",
            accessor: row => row.name,
            render: row => <Link className="text-accent" to={`/parts/${row.part_id}/info`}>{row.name}</Link>,
          },
          { key: "mpn", header: "MPN", accessor: row => row.mpn },
          { key: "on_hand", header: "On hand", accessor: row => row.on_hand, width: "90px" },
          { key: "currency", header: "Currency", accessor: row => row.currency ?? "-", width: "100px" },
          {
            key: "historical_cost",
            header: "Historical cost",
            accessor: row => row.historical_cost ?? historicalSummary(row),
            render: row => <span className="tabular-nums">{historicalSummary(row)}</span>,
          },
          {
            key: "replacement_cost",
            header: "Replacement cost",
            accessor: row => row.replacement_cost ?? "",
            render: row => <span className="tabular-nums">{moneyValue(row.replacement_cost, row.replacement_currency)}</span>,
          },
          {
            key: "delta_abs",
            header: "Delta",
            accessor: row => row.delta_abs ?? "",
            render: row => (
              <span className={`tabular-nums ${Number(row.delta_abs ?? 0) > 0 ? "text-warning" : ""}`}>
                {moneyValue(row.delta_abs, row.replacement_currency)}
              </span>
            ),
          },
          {
            key: "delta_pct",
            header: "Delta %",
            accessor: row => row.delta_pct ?? "",
            width: "100px",
            render: row => <span className="tabular-nums">{percentValue(row.delta_pct)}</span>,
          },
          {
            key: "reason",
            header: "Reason",
            accessor: row => reasonLabel(row.reason),
            render: row => row.reason ? <span className="pill bg-panel2 text-muted">{reasonLabel(row.reason)}</span> : <span className="text-muted">-</span>,
          },
          {
            key: "source",
            header: "Source",
            accessor: row => row.source ?? "",
            width: "110px",
            render: row => row.source === "trustedparts" ? <SourcingSourceLabel source="trustedparts" /> : <span className="text-muted">-</span>,
          },
        ]}
      />

      {data.totals.length > 0 && (
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Currency</th>
                <th>Historical cost</th>
                <th>Replacement cost</th>
                <th>Delta</th>
              </tr>
            </thead>
            <tbody>
              {data.totals.map(total => (
                <tr key={total.currency ?? "none"}>
                  <td>{total.currency ?? <span className="text-muted">-</span>}</td>
                  <td className="tabular-nums">{moneyValue(total.historical_cost, total.currency)}</td>
                  <td className="tabular-nums">{moneyValue(total.replacement_cost, total.currency)}</td>
                  <td className="tabular-nums">{moneyValue(total.delta_abs, total.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
