import { formatMoney, offerDisplayCurrency } from "./sourcingHelpers";
import type { SourcingBomResponse } from "./sourcingTypes";

export function CapacityBanner({ data, currency }: { data: SourcingBomResponse; currency?: string | null }) {
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
