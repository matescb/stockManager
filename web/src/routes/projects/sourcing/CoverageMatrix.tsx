import { DataTable, type Column } from "@/components/DataTable";
import { formatLeadTime, formatMoney, numberOrNull } from "./sourcingHelpers";
import type { CoverageRow, SourcingBomResponse } from "./sourcingTypes";

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

function formatCoveragePct(pct: number): string {
  return `${Math.floor(pct * 100)}%`;
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
  const coverageText = pct == null ? "—" : formatCoveragePct(pct);
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

export function CoverageMatrix({ data, currency }: { data: SourcingBomResponse; currency?: string | null }) {
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
      render: row => formatCoveragePct(row.coverage_pct),
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
      <h2 className="card-title">Coverage matrix</h2>
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
