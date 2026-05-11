import { useState } from "react";
import { AlertCircle, AlertTriangle, CheckCircle2, Circle, OctagonAlert } from "lucide-react";
import { DataTable, type Column } from "@/components/DataTable";
import { RiskLegendPopover } from "@/components/RiskLegendPopover";
import { SourcingSourceLabel } from "@/components/SourcingSourceLabel";
import { LIFECYCLE_LEGEND, SUPPLY_CHAIN_LEGEND } from "@/lib/riskLegends";
import { lifecycleRiskTone, type RiskTone } from "@/lib/sourcing";
import { BomDistributorsModal } from "./BomDistributorsModal";
import {
  formatLeadTime,
  formatMoney,
  legacyRiskFlags,
  lifecycleRiskClass,
  numberOrNull,
  offerDisplayCurrency,
  offerDisplayUnitPrice,
  riskClass,
  riskLabel,
  riskTooltip,
  rohsTone,
} from "./sourcingHelpers";
import type { SourcingBomLine } from "./sourcingTypes";

const RISK_TONE_ICONS = {
  good: CheckCircle2,
  "low-warning": AlertCircle,
  warning: AlertTriangle,
  danger: OctagonAlert,
  neutral: Circle,
} satisfies Record<RiskTone, typeof CheckCircle2>;

function RiskToneIcon({ tone }: { tone: RiskTone }) {
  const Icon = RISK_TONE_ICONS[tone];
  return <Icon size={12} aria-hidden="true" />;
}

function LifecycleRiskPill({ label = "Lifecycle risk", value }: { label?: string; value?: string | null }) {
  const trimmed = value?.trim();
  if (!trimmed) return null;
  const tone = lifecycleRiskTone(trimmed);
  return (
    <span
      className={`pill inline-flex items-center gap-1 ${lifecycleRiskClass(trimmed)}`}
      title={trimmed}
      aria-label={`${label}: ${trimmed}`}
    >
      <RiskToneIcon tone={tone} />
      {trimmed}
    </span>
  );
}

function RohsRiskPill({ tone }: { tone: "good" | "danger" | "neutral" }) {
  if (tone === "neutral") return <span className="text-muted">—</span>;
  return tone === "danger" ? (
    <span
      className="pill inline-flex items-center gap-1 bg-danger/10 text-danger"
      title={riskTooltip("rohs_non_compliant")}
      aria-label={riskTooltip("rohs_non_compliant")}
    >
      <RiskToneIcon tone="danger" />
      Non-compliant
    </span>
  ) : (
    <span
      className="pill inline-flex items-center gap-1 bg-success/10 text-success"
      title="TrustedParts found compliant EU RoHS data for this BOM line."
      aria-label="TrustedParts found compliant EU RoHS data for this BOM line."
    >
      <RiskToneIcon tone="good" />
      Compliant
    </span>
  );
}

export function BomRows({ rows, workspaceCurrency }: { rows: SourcingBomLine[]; workspaceCurrency: string | null }) {
  const [selectedLine, setSelectedLine] = useState<SourcingBomLine | null>(null);
  const columns: Column<SourcingBomLine>[] = [
    { key: "part", header: "Part", accessor: row => row.part_name },
    { key: "mpn", header: "MPN", accessor: row => row.mpn ?? "", render: row => row.mpn ?? "—" },
    { key: "required", header: "Required", accessor: row => row.required, align: "right" },
    { key: "available", header: "On hand", accessor: row => row.available + row.substitute_available, align: "right" },
    { key: "short", header: "Short", accessor: row => row.short_by, align: "right" },
    { key: "stock", header: "Authorized stock", accessor: row => row.authorized_stock, align: "right" },
    {
      key: "offer",
      header: "Best offer",
      accessor: row => numberOrNull(offerDisplayUnitPrice(row.best_offer)),
      render: row => row.best_offer
        ? formatMoney(offerDisplayUnitPrice(row.best_offer), offerDisplayCurrency(row.best_offer))
        : <span className="text-muted">—</span>,
      align: "right",
    },
    {
      key: "distributor",
      header: "Distributor",
      accessor: row => row.best_offer?.distributor ?? "",
      render: row => row.best_offer?.url ? (
        <a
          className="text-accent hover:underline"
          href={row.best_offer.url}
          target="_blank"
          rel="noopener noreferrer"
          onClick={event => event.stopPropagation()}
        >
          {row.best_offer.distributor}
        </a>
      ) : row.best_offer?.distributor ?? "—",
    },
    {
      key: "cost",
      header: "Est. cost",
      accessor: row => numberOrNull(row.est_extended_cost),
      render: row => formatMoney(row.est_extended_cost, row.best_offer?.currency),
      align: "right",
    },
    {
      key: "lead_time",
      header: "Lead time",
      accessor: row => row.lead_time_days,
      render: row => formatLeadTime(row.lead_time_days),
      align: "right",
      // SX-1/TPS-9: keep lead-time response data, but hide this crowded BOM column by default.
      hidden: true,
    },
    {
      key: "lifecycle",
      header: (
        <span className="inline-flex items-center gap-1">
          Lifecycle
          <RiskLegendPopover legend={LIFECYCLE_LEGEND} title="Lifecycle Risk Statuses" />
        </span>
      ),
      headerLabel: "Lifecycle",
      accessor: row => row.best_offer?.lifecycle_risk?.trim() ?? "",
      render: row => <LifecycleRiskPill value={row.best_offer?.lifecycle_risk} />,
    },
    {
      key: "supply_chain",
      header: (
        <span className="inline-flex items-center gap-1">
          Supply chain
          <RiskLegendPopover legend={SUPPLY_CHAIN_LEGEND} title="Supply Chain Risk Statuses" />
        </span>
      ),
      headerLabel: "Supply chain",
      accessor: row => row.best_offer?.supply_chain_risk?.trim() ?? "",
      render: row => (
        <LifecycleRiskPill label="Supply-chain risk" value={row.best_offer?.supply_chain_risk} />
      ),
    },
    {
      key: "rohs",
      header: "RoHS",
      accessor: row => rohsTone(row),
      render: row => <RohsRiskPill tone={rohsTone(row)} />,
    },
    {
      key: "risk",
      header: "Risk",
      accessor: row => row.risk_flags.join(" "),
      render: row => {
        const displayFlags = row.risk_flags.filter(flag => legacyRiskFlags.includes(flag));
        return (
          <span className="flex flex-wrap gap-1">
            {displayFlags.length === 0 ? (
              <span className="text-muted">—</span>
            ) : displayFlags.map(flag => (
              <span
                key={flag}
                className={riskClass(flag)}
                title={riskTooltip(flag)}
                aria-label={riskTooltip(flag)}
              >
                {riskLabel(flag)}
              </span>
            ))}
          </span>
        );
      },
    },
    {
      key: "source",
      header: "Source",
      accessor: () => "TrustedParts",
      render: () => <SourcingSourceLabel source="trustedparts" />,
    },
  ];

  return (
    <section className="space-y-2">
      <h2 className="text-md font-semibold">BOM rows</h2>
      <DataTable
        rows={rows}
        columns={columns}
        rowKey={row => row.project_entry_id}
        onRowClick={setSelectedLine}
        rowCanClick={row => row.offers.length > 0}
        tableId="project-sourcing-bom"
        exportFilename="sourced-bom"
      />
      <BomDistributorsModal
        open={selectedLine !== null}
        onClose={() => setSelectedLine(null)}
        line={selectedLine}
        workspaceCurrency={workspaceCurrency}
      />
    </section>
  );
}
