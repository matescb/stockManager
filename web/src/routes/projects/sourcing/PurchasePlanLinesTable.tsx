import { DataTable, quantityColumn, type Column } from "@/components/DataTable";
import { SourcingSourceLabel } from "@/components/SourcingSourceLabel";
import { isSafeHttpUrl } from "@/lib/url";
import {
  extendedCost,
  formatLeadTime,
  formatMoney,
  numberOrNull,
} from "./purchasePlanHelpers";
import type { PurchasePlanLine, PurchasePlanOrderOverride } from "./purchasePlanTypes";

type Props = {
  planId: string;
  grouped: [string, PurchasePlanLine[]][];
  unfilled: PurchasePlanLine[];
  currency: string | null;
  overrides: Record<string, PurchasePlanOrderOverride>;
  onOverride: (line: PurchasePlanLine) => void;
};

export default function PurchasePlanLinesTable({
  planId,
  grouped,
  unfilled,
  currency,
  overrides,
  onOverride,
}: Props) {
  const columns: Column<PurchasePlanLine>[] = [
    { key: "mpn", header: "MPN", accessor: line => line.mpn_searched },
    { key: "distributor", header: "Distributor", accessor: line => line.selected_distributor ?? "" },
    quantityColumn<PurchasePlanLine>({ key: "required", header: "Required", value: line => line.required_qty, align: "right" }),
    quantityColumn<PurchasePlanLine>({ key: "internal", header: "Internal", value: line => line.internal_available_qty, align: "right" }),
    quantityColumn<PurchasePlanLine>({ key: "shortage", header: "Shortage", value: line => line.shortage_qty, align: "right" }),
    { key: "qty", header: "Selected qty", accessor: line => line.selected_qty ?? 0, align: "right" },
    {
      key: "unit",
      header: "Unit price",
      accessor: line => numberOrNull(line.selected_unit_price),
      render: line => formatMoney(line.selected_unit_price, line.selected_currency),
      align: "right",
    },
    {
      key: "extended",
      header: "Extended",
      accessor: line => extendedCost(line),
      render: line => formatMoney(extendedCost(line), line.selected_currency),
      align: "right",
    },
    {
      key: "lead",
      header: "Lead time",
      accessor: line => line.selected_lead_time_days,
      render: line => formatLeadTime(line.selected_lead_time_days),
      align: "right",
    },
    {
      key: "risk",
      header: "Risk",
      accessor: line => line.risk_flags.join(" "),
      render: line => (
        <span className="flex flex-wrap gap-1">
          {line.risk_flags.length === 0 ? (
            <span className="text-muted">-</span>
          ) : line.risk_flags.map(flag => (
            <span key={flag} className="pill bg-warning/10 text-warning">
              {flag.replaceAll("_", " ")}
            </span>
          ))}
        </span>
      ),
    },
    {
      key: "link",
      header: "Offer",
      accessor: line => line.selected_url ?? "",
      render: line => {
        const safeSelectedUrl = isSafeHttpUrl(line.selected_url) ? line.selected_url : null;
        return safeSelectedUrl ? (
          <a className="text-accent hover:underline" href={safeSelectedUrl} target="_blank" rel="noopener noreferrer">
            Open
          </a>
        ) : "-";
      },
    },
    {
      key: "override",
      header: "Override",
      accessor: () => "",
      render: line => (
        <button
          type="button"
          className={overrides[line.id] ? "btn-primary" : "btn"}
          data-testid={`override-button-${line.id}`}
          onClick={() => onOverride(line)}
        >
          Override
        </button>
      ),
    },
  ];

  return (
    <>
      {grouped.map(([distributor, lines]) => {
        const subtotal = lines.reduce((sum, line) => sum + (extendedCost(line) ?? 0), 0);
        return (
          <details key={distributor} className="rounded-md border border-border bg-panel p-4" open>
            <summary className="cursor-pointer">
              <span className="font-medium">{distributor}</span>
              <span className="ml-2 text-sm text-muted">{lines.length} lines - {formatMoney(subtotal, currency)}</span>
              <span className="ml-2"><SourcingSourceLabel source="trustedparts" /></span>
            </summary>
            <div className="mt-3">
              <DataTable
                rows={lines}
                columns={columns}
                rowKey={line => line.id}
                tableId={`purchase-plan-${planId}-${distributor}`}
                exportFilename={`purchase-plan-${distributor}`}
              />
            </div>
          </details>
        );
      })}
      {unfilled.length > 0 && (
        <section className="rounded-md border border-danger/40 bg-panel p-4">
          <h2 className="card-title text-danger">Unfilled lines</h2>
          <DataTable
            rows={unfilled}
            columns={columns}
            rowKey={line => line.id}
            tableId={`purchase-plan-${planId}-unfilled`}
          />
        </section>
      )}
    </>
  );
}
