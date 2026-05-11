import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellPlus, FolderKanban, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import EmptyState from "@/components/EmptyState";
import { DataTable, type Column } from "@/components/DataTable";
import { PoweredByTrustedParts } from "@/components/PoweredByTrustedParts";
import { RiskLegendPopover } from "@/components/RiskLegendPopover";
import { SourcingSourceLabel } from "@/components/SourcingSourceLabel";
import { ApiError, api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { LIFECYCLE_LEGEND, SUPPLY_CHAIN_LEGEND } from "@/lib/riskLegends";
import { lifecycleRiskTone, riskToneClass } from "@/lib/sourcing";
import AlertFormModal from "@/routes/sourcing/alerts/AlertFormModal";
import type { Project } from "@/types";
import { BomDistributorsModal } from "./BomDistributorsModal";
import PurchasePlanOptionsModal from "./PurchasePlanOptionsModal";
import type { PurchasePlan, PurchasePlanRequest } from "./purchasePlanTypes";
import type { SourcingWorkspaceSettings } from "./SourceBomButton";

export type SourcingBomPriceBreak = {
  quantity: number;
  unit_price: string | number;
  currency?: string | null;
};

export type SourcingBomOffer = {
  mpn: string;
  distributor: string;
  sku?: string | null;
  stock: number;
  unit_price?: string | number | null;
  currency?: string | null;
  unit_price_converted?: string | number | null;
  currency_displayed?: string | null;
  fx_converted?: boolean | null;
  fx_rate_date?: string | null;
  packaging?: string | null;
  moq?: number | null;
  lead_time_days?: number | null;
  price_breaks?: SourcingBomPriceBreak[] | null;
  price_breaks_converted?: SourcingBomPriceBreak[] | null;
  url?: string | null;
  availability_text?: string | null;
  quantity_multiple?: number | null;
  lifecycle_risk?: string | null;
  supply_chain_risk?: string | null;
  is_affected_by_tariff?: boolean | null;
  rohs_compliance?: SourcingRohsCompliance[];
};

export type SourcingRohsCompliance = {
  region: string;
  is_compliant: boolean;
  description?: string | null;
};

type RiskFlag =
  | "single_source"
  | "no_authorized_stock"
  | "moq_overbuy"
  | "lead_time_long"
  | "preferred_distributor_unmet"
  | "lifecycle_risk_present"
  | "supply_chain_risk_present"
  | "tariff_affected"
  | "rohs_non_compliant";

export type SourcingBomLine = {
  project_entry_id: string;
  part_id: string;
  part_name: string;
  mpn?: string | null;
  required: number;
  available: number;
  substitute_ids: string[];
  substitute_available: number;
  short_by: number;
  authorized_stock: number;
  offers: SourcingBomOffer[];
  best_offer?: SourcingBomOffer | null;
  est_extended_cost?: string | number | null;
  lead_time_days?: number | null;
  cache_hit?: boolean | null;
  reason?: "ok" | "no_mpn" | "no_offers" | null;
  fx_status?: "unavailable" | null;
  risk_flags: RiskFlag[];
};

type CoverageRow = {
  distributor: string;
  lines_covered: number;
  lines_uncovered: string[];
  coverage_pct: number;
  est_total_cost?: string | number | null;
  worst_lead_time_days?: number | null;
};

type SourcingBomResponse = {
  rows: SourcingBomLine[];
  coverage: {
    rows: CoverageRow[];
    total_lines: number;
    best_single_distributor?: string | null;
    best_two_distributor_combo?: [string, string] | null;
    lowest_total_price_combo: string[];
    lowest_total_price_total?: string | number | null;
    fewest_distributors_combo: string[];
    fewest_distributors_total?: string | number | null;
    target_coverage_pct: number;
  };
  capacity: {
    can_build_now: number;
    can_build_after_purchase: number;
    total_bom_cost?: string | number | null;
    cost_per_single_bom?: string | number | null;
    purchase_to_pay_cost?: string | number | null;
    est_purchase_cost?: string | number | null;
    blocking_lines_now: string[];
    blocking_lines_after_purchase: string[];
  };
  build_quantity: number;
  powered_by: "TrustedParts";
  fetched_at: string;
  partial: boolean;
  fx_status?: "ok" | "partial" | "unavailable" | null;
  links: {
    primary: string;
    attribution: string;
  };
};

type SourcingRequest = {
  build_quantity: number;
  country?: string;
  currency?: string | null;
  distributors?: string[];
};

const SOURCING_BOM_GC_TIME_MS = 30 * 60 * 1000;

function numberOrNull(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatCount(value: number | null | undefined): string {
  return value == null ? "—" : value.toLocaleString();
}

function formatMoney(value: string | number | null | undefined, currency?: string | null): string {
  const numeric = numberOrNull(value);
  if (numeric == null) return "—";
  const formatted = numeric.toLocaleString(undefined, {
    maximumFractionDigits: 2,
    minimumFractionDigits: numeric % 1 === 0 ? 0 : 2,
  });
  return currency ? `${formatted} ${currency}` : formatted;
}

function offerDisplayUnitPrice(offer: SourcingBomOffer | null | undefined): string | number | null | undefined {
  if (!offer) return null;
  return offer.fx_converted === true && offer.unit_price_converted != null
    ? offer.unit_price_converted
    : offer.unit_price;
}

function offerDisplayCurrency(offer: SourcingBomOffer | null | undefined): string | null | undefined {
  if (!offer) return null;
  return offer.currency_displayed ?? offer.currency;
}

function formatLeadTime(days: number | null | undefined): string {
  if (days == null) return "—";
  return days === 1 ? "1 day" : `${days.toLocaleString()} days`;
}

function riskLabel(flag: RiskFlag): string {
  switch (flag) {
    case "single_source":
      return "Single source";
    case "no_authorized_stock":
      return "No authorized stock";
    case "moq_overbuy":
      return "MOQ overbuy";
    case "lead_time_long":
      return "Long lead time";
    case "preferred_distributor_unmet":
      return "Preferred unmet";
    case "lifecycle_risk_present":
      return "lifecycle";
    case "supply_chain_risk_present":
      return "supply chain";
    case "tariff_affected":
      return "tariff";
    case "rohs_non_compliant":
      return "RoHS";
  }
}

function riskClass(flag: RiskFlag): string {
  return flag === "rohs_non_compliant"
    ? "pill bg-danger/10 text-danger"
    : "pill bg-warning/10 text-warning";
}

function riskTooltip(flag: RiskFlag): string | undefined {
  switch (flag) {
    case "lifecycle_risk_present":
      return "TrustedParts returned lifecycle risk text for this BOM line.";
    case "supply_chain_risk_present":
      return "TrustedParts returned supply-chain risk text for this BOM line.";
    case "tariff_affected":
      return "TrustedParts distributors indicated this BOM line may be affected by United States tariffs.";
    case "rohs_non_compliant":
      return "TrustedParts did not find a compliant RoHS region for this BOM line.";
    default:
      return undefined;
  }
}

const legacyRiskFlags: RiskFlag[] = [
  "single_source",
  "no_authorized_stock",
  "moq_overbuy",
  "lead_time_long",
  "preferred_distributor_unmet",
  "tariff_affected",
];

function LifecycleRiskPill({ label = "Lifecycle risk", value }: { label?: string; value?: string | null }) {
  const trimmed = value?.trim();
  if (!trimmed) return null;
  return (
    <span className={`pill ${riskToneClass(lifecycleRiskTone(trimmed))}`} title={trimmed} aria-label={`${label}: ${trimmed}`}>
      {trimmed}
    </span>
  );
}

function rohsTone(row: SourcingBomLine): "good" | "danger" | "neutral" {
  if (row.risk_flags.includes("rohs_non_compliant")) return "danger";
  const euEntries = row.offers
    .flatMap(offer => offer.rohs_compliance ?? [])
    .filter(item => item.region.trim().toLowerCase() === "eu");
  if (euEntries.length === 0) return "neutral";
  if (euEntries.some(item => item.is_compliant === false)) return "danger";
  return "good";
}

function RohsRiskPill({ tone }: { tone: "good" | "danger" | "neutral" }) {
  if (tone === "neutral") return <span className="text-muted">—</span>;
  return tone === "danger" ? (
    <span
      className="pill bg-danger/10 text-danger"
      title={riskTooltip("rohs_non_compliant")}
      aria-label={riskTooltip("rohs_non_compliant")}
    >
      Non-compliant
    </span>
  ) : (
    <span
      className="pill bg-success/10 text-success"
      title="TrustedParts found compliant EU RoHS data for this BOM line."
      aria-label="TrustedParts found compliant EU RoHS data for this BOM line."
    >
      Compliant
    </span>
  );
}

function errorStatus(error: unknown): number | null {
  return error instanceof ApiError ? error.status : null;
}

function sourcingErrorToastMessage(error: unknown): string {
  if (errorStatus(error) === 429) return "Rate limit hit — wait a minute before sourcing again.";
  if (error instanceof ApiError) return error.userMessage;
  return "Failed to source BOM. Try again.";
}

function defaultFromActiveList(saved: string | null | undefined, active: string[]): string {
  if (saved && active.includes(saved)) return saved;
  return active[0] ?? "";
}

function distributorsFromActiveList(saved: string[] | null | undefined, active: string[]): string[] {
  if (!saved || saved.length === 0) return [];
  const intersection = saved.filter(item => active.includes(item));
  if (intersection.length > 0) return intersection;
  return active[0] ? [active[0]] : [];
}

function SourcingSkeleton() {
  return (
    <div className="space-y-3" role="status" aria-label="Loading sourced BOM">
      {[0, 1, 2].map(section => (
        <div key={section} className="card p-4 animate-pulse">
          <div className="h-4 w-48 rounded bg-panel2 mb-4" />
          <div className="space-y-2">
            {[0, 1, 2].map(row => (
              <div key={row} className="grid grid-cols-5 gap-3">
                {[0, 1, 2, 3, 4].map(cell => (
                  <div key={cell} className="h-3 rounded bg-panel2" />
                ))}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function EmptyBomState({ projectId }: { projectId: string }) {
  return (
    <EmptyState
      icon={FolderKanban}
      title="BOM is empty"
      description="Add BOM lines first to run sourcing coverage."
      action={{ label: "Add BOM lines first", to: `/projects/${projectId}/import` }}
    />
  );
}

function SourcingDiagnosticsPanel({
  data,
  projectId,
  status,
  onRefresh,
}: {
  data?: SourcingBomResponse;
  projectId: string;
  status: number | null;
  onRefresh: () => void;
}) {
  if (status === 409) {
    return (
      <div className="card p-4 space-y-3" role="status" aria-label="Sourcing diagnostics">
        <div>
          <div className="font-medium">Sourcing not configured.</div>
          <div className="text-sm text-muted">
            Sourcing cannot run until TrustedParts credentials and workspace defaults are configured.
          </div>
        </div>
        <Link className="btn" to="/settings/workspace">
          Open Settings → Sourcing
        </Link>
      </div>
    );
  }

  const rows = data?.rows ?? [];
  if (rows.length === 0 || rows.some(row => row.best_offer)) {
    return null;
  }

  const allNoMpn = rows.every(row => row.reason === "no_mpn" || !row.mpn);
  const allCacheHit = rows.every(row => row.cache_hit === true);
  const fxUnavailable = rows.some(row => row.fx_status === "unavailable");

  let title = "No matching offers found.";
  let description = "TrustedParts returned no authorized offers for the selected country, currency, and distributors.";

  if (allNoMpn) {
    title = "BOM lines need manufacturer part numbers.";
    description = "Add MPNs to these parts, then source the BOM again.";
  } else if (fxUnavailable) {
    title = "Prices were found, but currency conversion is unavailable.";
    description = "Retry later or choose the offer currency while exchange rates are unavailable.";
  } else if (allCacheHit) {
    title = "Only cached no-offer results were available.";
    description = "Refresh prices to check TrustedParts again for the current sourcing filters.";
  }

  return (
    <div className="card p-4 space-y-3" role="status" aria-label="Sourcing diagnostics">
      <div>
        <div className="font-medium">{title}</div>
        <div className="text-sm text-muted">{description}</div>
      </div>
      {allCacheHit && (
        <button type="button" className="btn" onClick={onRefresh}>
          <RefreshCw size={14} aria-hidden="true" />
          Refresh prices
        </button>
      )}
      {allNoMpn && (
        <Link className="btn" to={`/projects/${projectId}/import`}>
          Edit BOM
        </Link>
      )}
    </div>
  );
}

function BudgetState({
  disabledUntil,
  onRetry,
}: {
  disabledUntil: number | null;
  onRetry: () => void;
}) {
  const disabled = disabledUntil != null && Date.now() < disabledUntil;
  return (
    <div className="card p-4 text-sm text-muted" role="status">
      <div>TrustedParts request budget reached for this hour. Retry is paused for 5 minutes.</div>
      <button type="button" className="btn mt-3" disabled={disabled} onClick={onRetry}>
        Retry Source BOM
      </button>
    </div>
  );
}

function CapacityBanner({ data, currency }: { data: SourcingBomResponse; currency?: string | null }) {
  const blocking = data.capacity.blocking_lines_after_purchase.length > 0
    ? data.capacity.blocking_lines_after_purchase
    : data.capacity.blocking_lines_now;
  const linesById = new Map(data.rows.map(row => [row.project_entry_id, row]));
  const purchaseCurrency = (
    currency?.trim().toUpperCase() ||
    data.rows.map(row => offerDisplayCurrency(row.best_offer)).find(Boolean)
  ) ?? null;
  const totalCostNote = data.capacity.total_bom_cost == null
    ? "no pricing available on any line"
    : `x ${data.build_quantity.toLocaleString()} build${data.build_quantity === 1 ? "" : "s"}`;
  const singleBomCostNote = data.capacity.cost_per_single_bom == null
    ? "no pricing available on any line"
    : "one full build";
  const purchaseCostNote = data.capacity.purchase_to_pay_cost == null
    ? "no non-blocking priced shortages"
    : "short qty only, excluding blocking lines";

  return (
    <div className="card p-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
        <div>
          <div className="section-title">Can build now</div>
          <div className="text-2xl font-semibold tabular-nums">{data.capacity.can_build_now}</div>
        </div>
        <div>
          <div className="section-title">After purchase</div>
          <div className="text-2xl font-semibold tabular-nums">{data.capacity.can_build_after_purchase}</div>
        </div>
        <div className="sm:col-span-2 lg:col-span-2 min-w-0">
          <div className="section-title">Costs</div>
          <div className="mt-1 flex flex-col gap-1 text-sm">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="text-muted">Cost per 1 BOM:</span>
              <span className="font-mono tabular-nums">{formatMoney(data.capacity.cost_per_single_bom, purchaseCurrency)}</span>
              <span className="text-xs text-muted">{singleBomCostNote}</span>
            </div>
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="text-muted">Total BOM cost:</span>
              <span className="font-mono tabular-nums">{formatMoney(data.capacity.total_bom_cost, purchaseCurrency)}</span>
              <span className="text-xs text-muted">{totalCostNote}</span>
            </div>
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="text-muted">Price to pay:</span>
              <span className="font-mono font-semibold tabular-nums">
                {formatMoney(data.capacity.purchase_to_pay_cost, purchaseCurrency)}
              </span>
              <span className="text-xs text-muted">{purchaseCostNote}</span>
            </div>
          </div>
        </div>
        <div>
          <div className="section-title">Blocking lines</div>
          <div className="text-2xl font-semibold tabular-nums">{blocking.length}</div>
        </div>
      </div>
      {blocking.length > 0 && (
        <div className="mt-4 border-t border-border pt-3">
          <div className="section-title mb-2">Blocking lines</div>
          <div className="flex flex-wrap gap-2">
            {blocking.map(lineId => {
              const line = linesById.get(lineId);
              return (
                <span key={lineId} className="pill">
                  {line?.part_name ?? lineId}
                </span>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

type CoverageVariant = {
  labels: string[];
  combo: string[];
  total?: string | number | null;
};

function normalizedCombo(combo: string[]): string[] {
  return [...combo].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
}

function sameCombo(left: string[], right: string[]): boolean {
  const leftSorted = normalizedCombo(left);
  const rightSorted = normalizedCombo(right);
  return leftSorted.length === rightSorted.length && leftSorted.every((item, index) => item === rightSorted[index]);
}

function coverageForCombo(data: SourcingBomResponse, combo: string[]): { pct: number | null; shortLines: number | null } {
  if (combo.length === 0 || data.coverage.total_lines === 0) return { pct: null, shortLines: null };
  const comboSet = new Set(combo.map(item => item.toLocaleLowerCase()));
  const uncovered = new Set<string>();
  for (const row of data.coverage.rows) {
    if (!comboSet.has(row.distributor.toLocaleLowerCase())) continue;
    for (const lineId of row.lines_uncovered) uncovered.add(lineId);
  }
  const totalLineIds = new Set(data.rows.map(row => row.project_entry_id));
  for (const row of data.rows) {
    if (!uncovered.has(row.project_entry_id)) continue;
    const coveredByCombo = data.coverage.rows.some(coverageRow =>
      comboSet.has(coverageRow.distributor.toLocaleLowerCase()) &&
      !coverageRow.lines_uncovered.includes(row.project_entry_id),
    );
    if (coveredByCombo) uncovered.delete(row.project_entry_id);
  }
  const shortLines = [...totalLineIds].filter(lineId => uncovered.has(lineId)).length;
  return {
    pct: (data.coverage.total_lines - shortLines) / data.coverage.total_lines,
    shortLines,
  };
}

function CoverageVariantCard({
  data,
  variant,
  currency,
}: {
  data: SourcingBomResponse;
  variant: CoverageVariant;
  currency?: string | null;
}) {
  const { pct, shortLines } = coverageForCombo(data, variant.combo);
  const comboText = variant.combo.length > 0 ? normalizedCombo(variant.combo).join(" + ") : "—";
  const coverageText = pct == null ? "—" : `${Math.round(pct * 100)}%`;
  const hasPartialCoverage = shortLines != null && shortLines > 0;
  const priceLabel = hasPartialCoverage ? "Price (covered lines)" : "Price";
  const uncoveredText = hasPartialCoverage
    ? `${shortLines.toLocaleString()} uncovered line${shortLines === 1 ? "" : "s"}`
    : "";
  const hasNoCoveredLinePricing = variant.total == null && variant.combo.length > 0;
  const shortText = shortLines && shortLines > 0
    ? ` (${shortLines.toLocaleString()} line${shortLines === 1 ? "" : "s"} short)`
    : "";

  return (
    <div className="card p-4 space-y-3">
      <div className="flex flex-wrap gap-2">
        {variant.labels.map(label => (
          <span key={label} className="pill bg-accent/15 text-accent">{label}</span>
        ))}
      </div>
      <div className="text-lg font-semibold leading-snug">{comboText}</div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm">
        <div>
          <div className="section-title">{priceLabel}</div>
          <div className="font-mono tabular-nums">{formatMoney(variant.total, currency)}</div>
          {hasPartialCoverage && (
            <div className="text-xs text-muted">{uncoveredText}</div>
          )}
          {hasNoCoveredLinePricing && (
            <div className="text-xs text-muted">No pricing available on covered lines.</div>
          )}
        </div>
        <div>
          <div className="section-title">Coverage</div>
          <div className="font-mono tabular-nums">{coverageText}{shortText}</div>
        </div>
      </div>
      {variant.combo.length === 0 && (
        <div className="text-xs text-muted">No priced distributor combination is available for these BOM lines.</div>
      )}
    </div>
  );
}

function CoverageMatrix({ data, currency }: { data: SourcingBomResponse; currency?: string | null }) {
  const bestSingle = data.coverage.best_single_distributor;
  const bestTwo: string[] = data.coverage.best_two_distributor_combo ?? [];
  const lowestPrice: CoverageVariant = {
    labels: ["Lowest total price"],
    combo: data.coverage.lowest_total_price_combo ?? [],
    total: data.coverage.lowest_total_price_total,
  };
  const fewest: CoverageVariant = {
    labels: ["Fewest distributors"],
    combo: data.coverage.fewest_distributors_combo ?? [],
    total: data.coverage.fewest_distributors_total,
  };
  const variants = sameCombo(lowestPrice.combo, fewest.combo)
    ? [{ ...lowestPrice, labels: [...lowestPrice.labels, ...fewest.labels] }]
    : [lowestPrice, fewest];
  const columns: Column<CoverageRow>[] = [
    {
      key: "distributor",
      header: "Distributor",
      accessor: row => row.distributor,
      render: row => (
        <span className="flex flex-wrap items-center gap-2">
          <span>{row.distributor}</span>
          {row.distributor === bestSingle && <span className="pill bg-success/10 text-success">Best single distributor</span>}
          {bestTwo.includes(row.distributor) && <span className="pill bg-accent/15 text-accent">Best two-distributor combo</span>}
        </span>
      ),
    },
    { key: "lines", header: "Lines covered", accessor: row => row.lines_covered, align: "right" },
    {
      key: "coverage",
      header: "Coverage",
      accessor: row => row.coverage_pct,
      render: row => `${Math.round(row.coverage_pct * 100)}%`,
      align: "right",
    },
    {
      key: "cost",
      header: "Est. total cost",
      accessor: row => numberOrNull(row.est_total_cost),
      render: row => formatMoney(row.est_total_cost, currency),
      align: "right",
    },
    {
      key: "lead",
      header: "Worst lead time",
      accessor: row => row.worst_lead_time_days,
      render: row => formatLeadTime(row.worst_lead_time_days),
      align: "right",
    },
  ];

  return (
    <section className="space-y-2">
      <h2 className="text-md font-semibold">Coverage matrix</h2>
      <div className={`grid grid-cols-1 ${variants.length > 1 ? "lg:grid-cols-2" : ""} gap-3`}>
        {variants.map(variant => (
          <CoverageVariantCard
            key={variant.labels.join("-")}
            data={data}
            variant={variant}
            currency={currency}
          />
        ))}
      </div>
      <div className="text-xs text-muted">
        Coverage is the primary criterion; the two variants above optimize for cost vs. simplicity within full-coverage solutions.
      </div>
      <DataTable
        rows={data.coverage.rows}
        columns={columns}
        rowKey={row => row.distributor}
        tableId="project-sourcing-coverage"
        exportFilename="sourcing-coverage"
        empty={<div className="text-muted">No distributor coverage.</div>}
      />
    </section>
  );
}

function BomRows({ rows, workspaceCurrency }: { rows: SourcingBomLine[]; workspaceCurrency: string | null }) {
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

export default function ProjectSourcingPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [buildQuantity, setBuildQuantity] = useState(1);
  const [country, setCountry] = useState("");
  const [currency, setCurrency] = useState("");
  const [distributors, setDistributors] = useState<string[]>([]);
  const [defaultsApplied, setDefaultsApplied] = useState(false);
  const [budgetDisabledUntil, setBudgetDisabledUntil] = useState<number | null>(null);
  const [planModalOpen, setPlanModalOpen] = useState(false);
  const [alertModalOpen, setAlertModalOpen] = useState(false);
  const [planPending, setPlanPending] = useState(false);

  const { data: workspace } = useQuery({
    queryKey: useWsKey("ws", "current"),
    queryFn: () => api.get<SourcingWorkspaceSettings>("/workspaces/current"),
  });
  const { data: project } = useQuery({
    queryKey: useWsKey("project", projectId),
    queryFn: () => api.get<Project>(`/projects/${projectId}`),
    enabled: !!projectId,
  });

  useEffect(() => {
    if (!workspace || defaultsApplied) return;
    setCountry(defaultFromActiveList(workspace.sourcing_country_code, workspace.active_countries));
    setCurrency(defaultFromActiveList(workspace.sourcing_currency_code, workspace.active_currencies));
    setDistributors(distributorsFromActiveList(
      workspace.sourcing_preferred_distributors,
      workspace.active_distributors,
    ));
    setDefaultsApplied(true);
  }, [workspace, defaultsApplied]);

  const filterWarnings = useMemo(() => {
    if (!workspace || !defaultsApplied) return [];
    const warnings: string[] = [];
    if (
      workspace.active_countries.length > 0 &&
      workspace.sourcing_country_code &&
      !workspace.active_countries.includes(workspace.sourcing_country_code)
    ) {
      warnings.push(`Workspace default country is not active; using ${workspace.active_countries[0]}.`);
    }
    if (
      workspace.active_currencies.length > 0 &&
      workspace.sourcing_currency_code &&
      !workspace.active_currencies.includes(workspace.sourcing_currency_code)
    ) {
      warnings.push(`Workspace default currency is not active; using ${workspace.active_currencies[0]}.`);
    }
    const preferred = workspace.sourcing_preferred_distributors ?? [];
    if (
      workspace.active_distributors.length > 0 &&
      preferred.length > 0 &&
      preferred.some(item => !workspace.active_distributors.includes(item))
    ) {
      warnings.push("Workspace preferred distributors are not all active; using active distributors only.");
    }
    return warnings;
  }, [workspace, defaultsApplied]);

  const activeListErrors = useMemo(() => {
    if (!workspace) return [];
    const errors: string[] = [];
    if (workspace.active_countries.length === 0) errors.push("No active countries configured.");
    if (workspace.active_currencies.length === 0) errors.push("No active currencies configured.");
    if (workspace.active_distributors.length === 0) errors.push("No active distributors configured.");
    return errors;
  }, [workspace]);

  const requestBody = useMemo<SourcingRequest>(() => {
    const cleanWorkspaceCurrency = workspace?.sourcing_currency_code?.trim().toUpperCase() || null;
    const body: SourcingRequest = {
      build_quantity: Math.max(1, Math.floor(buildQuantity || 1)),
      currency: cleanWorkspaceCurrency,
    };
    const cleanCountry = country.trim().toUpperCase();
    const cleanCurrency = currency.trim().toUpperCase();
    const cleanDistributors = distributors.filter(item => item.trim());
    if (cleanCountry) body.country = cleanCountry;
    if (cleanWorkspaceCurrency && cleanCurrency) body.currency = cleanCurrency;
    if (cleanDistributors.length > 0) body.distributors = cleanDistributors;
    return body;
  }, [buildQuantity, country, currency, distributors, workspace?.sourcing_currency_code]);

  const sourcingDisplayCacheKey = useWsKey("project-sourcing", projectId);
  const cachedSourcing = useQuery<SourcingBomResponse | null>({
    queryKey: sourcingDisplayCacheKey,
    queryFn: async () => null,
    enabled: false,
    staleTime: Infinity,
    gcTime: SOURCING_BOM_GC_TIME_MS,
  });
  const sourcing = useMutation<SourcingBomResponse, unknown, SourcingRequest>({
    mutationFn: body =>
      api.post<SourcingBomResponse, SourcingRequest>(`/projects/${projectId}/sourcing`, body),
    onSuccess: result => {
      queryClient.setQueryData(sourcingDisplayCacheKey, result);
    },
    onError: error => {
      const status = errorStatus(error);
      if (status === 503) setBudgetDisabledUntil(Date.now() + 5 * 60 * 1000);
      toast.error(sourcingErrorToastMessage(error));
    },
  });
  const sourcingData = sourcing.data ?? cachedSourcing.data ?? undefined;
  const purchasePlanBaseRequest = useMemo<Omit<PurchasePlanRequest, "strategy">>(() => {
    const body = { ...requestBody };
    if (body.currency == null) delete body.currency;
    return body as Omit<PurchasePlanRequest, "strategy">;
  }, [requestBody]);

  useEffect(() => {
    if (budgetDisabledUntil == null) return;
    const delay = Math.max(0, budgetDisabledUntil - Date.now());
    const timeout = window.setTimeout(() => setBudgetDisabledUntil(null), delay);
    return () => window.clearTimeout(timeout);
  }, [budgetDisabledUntil]);

  const status = errorStatus(sourcing.error);
  const sourceBlocked =
    buildQuantity < 1 ||
    !defaultsApplied ||
    activeListErrors.length > 0 ||
    (workspace ? !workspace.active_countries.includes(country) : false) ||
    (workspace ? !workspace.active_currencies.includes(currency) : false) ||
    distributors.some(distributor => workspace ? !workspace.active_distributors.includes(distributor) : false) ||
    (budgetDisabledUntil != null && Date.now() < budgetDisabledUntil);
  const sourceDisabled = sourcing.isPending || sourceBlocked;
  const hasRows = (sourcingData?.rows.length ?? 0) > 0;
  const primaryUrl = sourcingData?.links.primary;

  function runSourcing() {
    if (!projectId || sourceDisabled) return;
    sourcing.mutate(requestBody);
  }

  async function generatePurchasePlan(planRequest: PurchasePlanRequest) {
    if (!projectId) return;
    setPlanPending(true);
    try {
      const plan = await api.post<PurchasePlan, PurchasePlanRequest>(
        `/projects/${projectId}/purchase-plan`,
        planRequest,
      );
      setPlanModalOpen(false);
      navigate(`/projects/${projectId}/purchase-plans/${plan.id}`, {
        state: { plan, project },
      });
    } finally {
      setPlanPending(false);
    }
  }

  if (!projectId) return null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm text-muted">Projects · {project?.name ?? "Project"} · Sourcing</div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold">Source BOM</h1>
            {sourcingData?.partial && <span className="pill bg-warning/10 text-warning">Partial — some chunks served from cache</span>}
          </div>
        </div>
        <PoweredByTrustedParts primaryUrl={primaryUrl} />
      </div>

      <div className="card p-4">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
          <div>
            <label className="label" htmlFor="sourcing-build-quantity">Build quantity</label>
            <input
              id="sourcing-build-quantity"
              className="input"
              type="number"
              min={1}
              step={1}
              value={buildQuantity}
              onChange={event => setBuildQuantity(Number(event.target.value))}
            />
          </div>
          <div>
            <label className="label" htmlFor="sourcing-country">Country</label>
            <select
              id="sourcing-country"
              className="input uppercase"
              value={country}
              onChange={event => setCountry(event.target.value)}
              disabled={(workspace?.active_countries.length ?? 0) === 0}
            >
              {(workspace?.active_countries ?? []).map(item => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="sourcing-currency">Currency</label>
            <select
              id="sourcing-currency"
              className="input uppercase"
              value={currency}
              onChange={event => setCurrency(event.target.value)}
              disabled={(workspace?.active_currencies.length ?? 0) === 0}
            >
              {(workspace?.active_currencies ?? []).map(item => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
          </div>
          <fieldset className="md:col-span-2">
            <legend className="label">Distributors</legend>
            <div className="max-h-52 overflow-auto rounded border border-border p-2">
              {(workspace?.active_distributors ?? []).map(item => (
                <label key={item} className="flex items-center gap-2 py-1 text-sm">
                  <input
                    type="checkbox"
                    checked={distributors.includes(item)}
                    onChange={event => {
                      setDistributors(current => event.target.checked
                        ? [...current, item]
                        : current.filter(distributor => distributor !== item));
                    }}
                  />
                  <span>{item}</span>
                </label>
              ))}
              {(workspace?.active_distributors.length ?? 0) === 0 && (
                <div className="text-xs text-muted py-1">No active distributors configured.</div>
              )}
            </div>
          </fieldset>
        </div>
        {activeListErrors.length > 0 && (
          <div className="mt-3 text-xs text-muted" role="status">
            {activeListErrors.join(" ")} Open Settings → Workspace to update active lists.
          </div>
        )}
        {filterWarnings.length > 0 && (
          <div className="mt-3 text-xs text-warning" role="status">
            {filterWarnings.join(" ")}
          </div>
        )}
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          <button
            type="button"
            className="btn"
            disabled={!projectId}
            onClick={() => setAlertModalOpen(true)}
          >
            <BellPlus size={14} aria-hidden="true" />
            Set BOM-buyable alert
          </button>
          <button
            type="button"
            className="btn"
            disabled={sourceDisabled || !hasRows}
            onClick={() => setPlanModalOpen(true)}
          >
            Generate purchase plan
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={sourceDisabled}
            onClick={runSourcing}
          >
            <RefreshCw size={14} className={sourcing.isPending ? "animate-spin" : ""} />
            {sourcing.isPending ? "Sourcing…" : "Source"}
          </button>
        </div>
      </div>

      {sourcing.isPending && !sourcingData && <SourcingSkeleton />}
      {sourcing.isPending && sourcingData && (
        <div className="text-xs text-muted" role="status">
          Refreshing prices in the background...
        </div>
      )}
      {status === 503 && <BudgetState disabledUntil={budgetDisabledUntil} onRetry={runSourcing} />}
      {status === 502 && (
        <div className="card p-4" role="status">
          <button type="button" className="btn" onClick={runSourcing}>
            Retry Source BOM
          </button>
        </div>
      )}
      {sourcing.isError && status !== 409 && status !== 502 && status !== 503 && (
        <div className="card p-4 text-sm text-danger">Failed to source BOM.</div>
      )}

      {sourcingData && !hasRows && <EmptyBomState projectId={projectId} />}
      <SourcingDiagnosticsPanel
        data={sourcingData}
        projectId={projectId}
        status={status}
        onRefresh={runSourcing}
      />

      {sourcingData && hasRows && (
        <>
          <CapacityBanner data={sourcingData} currency={requestBody.currency} />
          <CoverageMatrix data={sourcingData} currency={requestBody.currency} />
          <BomRows rows={sourcingData.rows} workspaceCurrency={requestBody.currency ?? workspace?.sourcing_currency_code ?? null} />
          <div className="text-xs text-muted">
            {formatCount(sourcingData.rows.length)} line{sourcingData.rows.length === 1 ? "" : "s"} fetched from {sourcingData.powered_by}.
          </div>
        </>
      )}
      <PurchasePlanOptionsModal
        open={planModalOpen}
        buildQuantity={buildQuantity}
        baseRequest={purchasePlanBaseRequest}
        pending={planPending}
        onClose={() => setPlanModalOpen(false)}
        onSubmit={generatePurchasePlan}
      />
      <AlertFormModal
        open={alertModalOpen}
        title="Set BOM-buyable alert"
        initialValues={{ alert_type: "bom_buyable", project_id: projectId, build_quantity: Math.max(1, Math.floor(buildQuantity || 1)) }}
        allowedTypes={["bom_buyable"]}
        onClose={() => setAlertModalOpen(false)}
        onSaved={() => toast.success("Alert created.")}
      />
    </div>
  );
}
