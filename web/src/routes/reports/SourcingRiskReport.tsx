import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, BarChart3 } from "lucide-react";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import { PoweredByTrustedParts } from "@/components/PoweredByTrustedParts";
import { SourcingSourceLabel } from "@/components/SourcingSourceLabel";
import { api, ApiError } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { useWsKey } from "@/lib/queryKeys";
import { lifecycleRiskRank, lifecycleRiskTone } from "@/lib/sourcing";

type SourcingRiskFlag =
  | "single_source"
  | "no_authorized_stock"
  | "moq_overbuy"
  | "lead_time_long"
  | "preferred_distributor_unmet"
  | "lifecycle_risk_present"
  | "supply_chain_risk_present"
  | "tariff_affected"
  | "rohs_non_compliant"
  | "price_delta";

type SourcingRiskOffer = {
  mpn: string;
  distributor: string;
  sku: string | null;
  stock: number;
  unit_price: string | null;
  currency: string | null;
  packaging: string | null;
  moq: number | null;
  lead_time_days: number | null;
  url: string | null;
  lifecycle_risk?: string | null;
  supply_chain_risk?: string | null;
  is_affected_by_tariff?: boolean | null;
};

type SourcingRiskRow = {
  part_id: string;
  name: string;
  manufacturer: string | null;
  mpn: string;
  on_hand: number;
  distributors_with_stock: string[];
  authorized_stock: number;
  best_offer: SourcingRiskOffer | null;
  lead_time_days: number | null;
  typical_reorder_quantity: number;
  historical_unit_cost: string | null;
  historical_currency: string | null;
  price_delta_pct: string | null;
  risk_flags: SourcingRiskFlag[];
};

type SourcingRiskReportOut = {
  rows: SourcingRiskRow[];
  sourcing_status: {
    state: "ok" | "not_configured" | "budget_blocked" | "upstream_error";
    message: string;
  };
  powered_by: "TrustedParts";
  fetched_at: string;
  partial: boolean;
  cache_hit: boolean | null;
  links: {
    primary: string;
    attribution: string;
  };
};

const flagLabels: Record<SourcingRiskFlag, string> = {
  single_source: "Single source",
  no_authorized_stock: "No authorized stock",
  moq_overbuy: "MOQ overbuy",
  lead_time_long: "Long lead time",
  preferred_distributor_unmet: "Preferred unmet",
  lifecycle_risk_present: "Lifecycle",
  supply_chain_risk_present: "Supply chain",
  tariff_affected: "Tariff",
  rohs_non_compliant: "RoHS",
  price_delta: "Price delta",
};

const filterFlags: SourcingRiskFlag[] = [
  "single_source",
  "no_authorized_stock",
  "moq_overbuy",
  "lead_time_long",
  "preferred_distributor_unmet",
  "lifecycle_risk_present",
  "supply_chain_risk_present",
  "tariff_affected",
  "rohs_non_compliant",
  "price_delta",
];

function bestOfferLabel(offer: SourcingRiskOffer | null): string {
  if (!offer) return "—";
  const price = offer.unit_price ? formatMoney(Number(offer.unit_price), offer.currency) : "—";
  return `${offer.distributor} · ${price}`;
}

function flagClass(flag: SourcingRiskFlag): string {
  return flag === "rohs_non_compliant"
    ? "pill bg-danger/10 text-danger"
    : "pill bg-warning/15 text-warning";
}

function LifecycleRiskPill({ value }: { value?: string | null }) {
  const trimmed = value?.trim();
  if (!trimmed) return null;
  return (
    <span className={`pill ${lifecycleRiskTone(trimmed)}`} aria-label={`Lifecycle risk: ${trimmed}`}>
      {trimmed}
    </span>
  );
}

function statusTone(state: SourcingRiskReportOut["sourcing_status"]["state"]): string {
  switch (state) {
    case "ok":
      return "border-success/30 bg-success/10 text-success";
    case "not_configured":
      return "border-warning/30 bg-warning/10 text-warning";
    default:
      return "border-danger/30 bg-danger/10 text-danger";
  }
}

export default function SourcingRiskReport() {
  const [onlyWithFlags, setOnlyWithFlags] = useState(true);
  const [selectedFlags, setSelectedFlags] = useState<SourcingRiskFlag[]>([]);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: useWsKey("report", "sourcing-risk", onlyWithFlags),
    queryFn: () =>
      api.get<SourcingRiskReportOut>(
        `/reports/sourcing-risk?only_with_flags=${String(onlyWithFlags)}`
      ),
  });

  const rows = useMemo(
    () =>
      (data?.rows ?? []).filter(row =>
        selectedFlags.every(flag => row.risk_flags.includes(flag))
      ).sort((a, b) => {
        const byFlags = b.risk_flags.length - a.risk_flags.length;
        if (byFlags !== 0) return byFlags;
        return a.name.localeCompare(b.name);
      }),
    [data?.rows, selectedFlags],
  );

  function toggleFlag(flag: SourcingRiskFlag) {
    setSelectedFlags(current =>
      current.includes(flag) ? current.filter(item => item !== flag) : [...current, flag]
    );
  }

  if (isError) {
    return (
      <div className="text-danger text-sm p-4">
        Failed to load sourcing risk report. {error instanceof ApiError ? error.userMessage : ""}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <label className="inline-flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={onlyWithFlags}
            onChange={e => setOnlyWithFlags(e.target.checked)}
          />
          Show only flagged
        </label>
        {data && <PoweredByTrustedParts primaryUrl={data.links.primary} />}
      </div>

      <div className="flex flex-wrap gap-2" aria-label="Sourcing risk filters">
        {filterFlags.map(flag => {
          const active = selectedFlags.includes(flag);
          return (
            <button
              key={flag}
              type="button"
              className={active ? "pill bg-accent/15 text-accent" : "pill"}
              aria-pressed={active}
              onClick={() => toggleFlag(flag)}
            >
              {flagLabels[flag]}
            </button>
          );
        })}
      </div>

      {data && (
        <div className={`rounded-md border px-3 py-2 text-sm ${statusTone(data.sourcing_status.state)}`}>
          <div className="flex items-center gap-2">
            <AlertTriangle size={14} />
            <span>{data.sourcing_status.message}</span>
            {data.partial && <span className="text-muted">Partial</span>}
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="text-muted">Loading…</div>
      ) : (
        <DataTable
          rows={rows}
          rowKey={r => r.part_id}
          tableId="report-sourcing-risk"
          empty={
            <EmptyState
              icon={BarChart3}
              title="All clear"
              description="No parts match this sourcing-risk view."
            />
          }
          exportFilename="sourcing-risk"
          columns={[
            {
              key: "name",
              header: "Part",
              accessor: r => r.name,
              render: r => <Link className="text-accent" to={`/parts/${r.part_id}/info`}>{r.name}</Link>,
            },
            { key: "mpn", header: "MPN", accessor: r => r.mpn },
            { key: "on_hand", header: "On hand", accessor: r => r.on_hand, width: "90px" },
            {
              key: "distributors",
              header: "Stocked distributors",
              accessor: r => r.distributors_with_stock.join(", "),
              render: r => (
                <div className="flex flex-wrap gap-1">
                  {r.distributors_with_stock.length
                    ? r.distributors_with_stock.map(distributor => (
                      <span key={distributor} className="pill">{distributor}</span>
                    ))
                    : <span className="text-muted">—</span>}
                </div>
              ),
            },
            {
              key: "best_offer",
              header: "Best offer",
              accessor: r => bestOfferLabel(r.best_offer),
              render: r => (
                <div className="flex flex-col gap-1">
                  <span>{bestOfferLabel(r.best_offer)}</span>
                  {r.best_offer && <SourcingSourceLabel source="trustedparts" className="w-fit" />}
                </div>
              ),
            },
            {
              key: "lead_time",
              header: "Lead time",
              accessor: r => r.lead_time_days ?? "",
              width: "100px",
              render: r => r.lead_time_days == null ? <span className="text-muted">—</span> : <span>{r.lead_time_days}d</span>,
            },
            {
              key: "lifecycle",
              header: "Lifecycle",
              accessor: r => lifecycleRiskRank(r.best_offer?.lifecycle_risk),
              render: r => <LifecycleRiskPill value={r.best_offer?.lifecycle_risk} />,
              width: "110px",
            },
            {
              key: "flags",
              header: "Flags",
              accessor: r => r.risk_flags.length,
              render: r => (
                <div className="flex flex-wrap gap-1">
                  {r.risk_flags.length
                    ? r.risk_flags.map(flag => (
                      <span key={flag} className={flagClass(flag)}>{flagLabels[flag]}</span>
                    ))
                    : <span className="text-muted">—</span>}
                </div>
              ),
            },
            {
              key: "action",
              header: "",
              render: r => (
                <Link className="btn btn-sm whitespace-nowrap" to={`/parts/${r.part_id}/sourcing`}>
                  Source BOM
                </Link>
              ),
              width: "90px",
            },
          ]}
        />
      )}
    </div>
  );
}
