import { useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { RefreshCw, ShoppingCart } from "lucide-react";
import { toast } from "sonner";
import { DataTable, type Column } from "@/components/DataTable";
import { PoweredByTrustedParts } from "@/components/PoweredByTrustedParts";
import { SourcingSourceLabel } from "@/components/SourcingSourceLabel";
import { ApiError, api } from "@/lib/api";
import type { Project } from "@/types";
import OverrideOfferModal from "./OverrideOfferModal";
import type {
  ConvertOrdersRequest,
  ConvertOrdersResponse,
  PurchasePlan,
  PurchasePlanLine,
  PurchasePlanOffer,
  PurchasePlanOrderOverride,
} from "./purchasePlanTypes";

type LocationState = {
  plan?: PurchasePlan;
  project?: Project;
};

const STALE_MS = 10 * 60 * 1000;

function numberOrNull(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatMoney(value: string | number | null | undefined, currency?: string | null): string {
  const numeric = numberOrNull(value);
  if (numeric == null) return "-";
  const formatted = numeric.toLocaleString(undefined, {
    maximumFractionDigits: 2,
    minimumFractionDigits: numeric % 1 === 0 ? 0 : 2,
  });
  return currency ? `${formatted} ${currency}` : formatted;
}

function formatLeadTime(days: number | null | undefined): string {
  if (days == null) return "-";
  return days === 1 ? "1 day" : `${days.toLocaleString()} days`;
}

function refreshedLabel(plan: PurchasePlan): string {
  if (!plan.last_refreshed_at) return "Not refreshed yet";
  const ageMs = Date.now() - new Date(plan.last_refreshed_at).getTime();
  const minutes = Math.max(0, Math.floor(ageMs / 60000));
  return `Refreshed ${minutes} min ago`;
}

function isRefreshFresh(plan: PurchasePlan): boolean {
  if (!plan.last_refreshed_at) return false;
  return Date.now() - new Date(plan.last_refreshed_at).getTime() <= STALE_MS;
}

function extendedCost(line: PurchasePlanLine): number | null {
  const unit = numberOrNull(line.selected_unit_price);
  if (unit == null || line.selected_qty == null) return null;
  return unit * line.selected_qty;
}

function selectedQtyForOffer(line: PurchasePlanLine, offer: PurchasePlanOffer): number {
  const shortage = Math.max(0, line.shortage_qty);
  const moq = Math.max(0, numberOrNull(offer.moq) ?? 0);
  return Math.max(shortage, moq, 1);
}

function unitPriceForOffer(offer: PurchasePlanOffer, qty: number): string | number | null {
  const breaks = (offer.price_breaks ?? [])
    .map(priceBreak => ({
      quantity: numberOrNull(priceBreak.quantity),
      unitPrice: priceBreak.unit_price,
    }))
    .filter((priceBreak): priceBreak is { quantity: number; unitPrice: string | number } =>
      priceBreak.quantity != null &&
      priceBreak.quantity >= 1 &&
      priceBreak.unitPrice != null &&
      priceBreak.unitPrice !== "",
    )
    .sort((a, b) => a.quantity - b.quantity);

  if (breaks.length === 0) return offer.unit_price ?? null;

  let selected = breaks[0];
  for (const candidate of breaks) {
    if (candidate.quantity > qty) break;
    selected = candidate;
  }
  return selected.unitPrice;
}

function recomputePlanFromLines(plan: PurchasePlan, lines: PurchasePlanLine[]): PurchasePlan {
  const distributors = new Set<string>();
  let estTotal = 0;
  let hasCost = false;
  let worstLeadTime: number | null = null;
  let unfilledCount = 0;

  for (const line of lines) {
    if (line.selected_distributor) {
      distributors.add(line.selected_distributor);
    } else {
      unfilledCount += 1;
    }

    const cost = extendedCost(line);
    if (cost != null) {
      estTotal += cost;
      hasCost = true;
    }

    if (line.selected_lead_time_days != null) {
      worstLeadTime = Math.max(worstLeadTime ?? 0, line.selected_lead_time_days);
    }
  }

  return {
    ...plan,
    lines,
    distributors_used: [...distributors].sort((a, b) => a.localeCompare(b)),
    est_total_cost: hasCost ? estTotal : plan.est_total_cost,
    worst_lead_time_days: worstLeadTime,
    unfilled_count: unfilledCount,
  };
}

function groupLines(lines: PurchasePlanLine[]) {
  const groups = new Map<string, PurchasePlanLine[]>();
  for (const line of lines) {
    if (!line.selected_distributor) continue;
    const current = groups.get(line.selected_distributor) ?? [];
    current.push(line);
    groups.set(line.selected_distributor, current);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

function summaryCurrency(plan: PurchasePlan): string | null {
  return plan.lines.find(line => line.selected_currency)?.selected_currency ?? plan.currency_code ?? null;
}

export default function PurchasePlanReviewPage() {
  const { projectId, planId } = useParams<{ projectId: string; planId: string }>();
  const navigate = useNavigate();
  const { state } = useLocation();
  const locationState = (state ?? {}) as LocationState;
  const [plan, setPlan] = useState<PurchasePlan | null>(locationState.plan ?? null);
  const [project] = useState<Project | undefined>(locationState.project);
  const [busyAction, setBusyAction] = useState<"refresh" | "convert" | null>(null);
  const [overrideLine, setOverrideLine] = useState<PurchasePlanLine | null>(null);
  const [overrides, setOverrides] = useState<Record<string, PurchasePlanOrderOverride>>({});

  const grouped = useMemo(() => groupLines(plan?.lines ?? []), [plan]);
  const unfilled = useMemo(
    () => (plan?.lines ?? []).filter(line => !line.selected_distributor),
    [plan],
  );
  const activeOverrideLine = useMemo(
    () => overrideLine ? plan?.lines.find(line => line.id === overrideLine.id) ?? overrideLine : null,
    [overrideLine, plan],
  );

  if (!projectId || !planId) return null;
  if (!plan) {
    return (
      <div className="card p-4">
        <div className="font-medium">Purchase plan data is not loaded.</div>
        <button type="button" className="btn mt-3" onClick={() => navigate(`/projects/${projectId}/sourcing`)}>
          Back to sourcing
        </button>
      </div>
    );
  }

  const fresh = isRefreshFresh(plan);
  const currency = summaryCurrency(plan);
  const columns: Column<PurchasePlanLine>[] = [
    { key: "mpn", header: "MPN", accessor: line => line.mpn_searched },
    { key: "required", header: "Required", accessor: line => line.required_qty, align: "right" },
    { key: "internal", header: "Internal", accessor: line => line.internal_available_qty, align: "right" },
    { key: "shortage", header: "Shortage", accessor: line => line.shortage_qty, align: "right" },
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
      render: line => line.selected_url ? (
        <a className="text-accent hover:underline" href={line.selected_url} target="_blank" rel="noopener noreferrer">
          Open
        </a>
      ) : "-",
    },
    {
      key: "override",
      header: "Override",
      accessor: () => "",
      render: line => (
        <button
          type="button"
          className={overrides[line.id] ? "btn-primary" : "btn"}
          onClick={() => setOverrideLine(line)}
        >
          Override
        </button>
      ),
    },
  ];

  async function refresh() {
    if (!plan) return;
    setBusyAction("refresh");
    try {
      const next = await api.post<PurchasePlan>(`/sourcing/purchase-plans/${plan.id}/refresh`);
      setPlan(next);
      toast.success("Prices refreshed");
    } catch (err) {
      const message = err instanceof ApiError && err.userMessage ? err.userMessage : "Failed to refresh prices";
      toast.error(message);
    } finally {
      setBusyAction(null);
    }
  }

  function selectOffer(line: PurchasePlanLine, offer: PurchasePlanOffer) {
    if (!offer.distributor || offer.unit_price == null || !offer.currency) return;

    const selected_qty = selectedQtyForOffer(line, offer);
    const selected_unit_price = unitPriceForOffer(offer, selected_qty);
    if (selected_unit_price == null) return;

    const override: PurchasePlanOrderOverride = {
      selected_distributor: offer.distributor,
      selected_qty,
      selected_unit_price,
      selected_currency: offer.currency,
    };

    setPlan(current => {
      if (!current) return current;
      const nextLines = current.lines.map(currentLine =>
        currentLine.id === line.id
          ? {
              ...currentLine,
              selected_distributor: offer.distributor,
              selected_qty,
              selected_unit_price,
              selected_currency: offer.currency,
              selected_packaging: offer.packaging ?? null,
              selected_moq: offer.moq ?? null,
              selected_lead_time_days: offer.lead_time_days ?? null,
              selected_url: offer.url ?? null,
            }
          : currentLine,
      );
      return recomputePlanFromLines(current, nextLines);
    });
    setOverrides(current => ({ ...current, [line.id]: override }));
    setOverrideLine(null);
  }

  async function convert() {
    if (!plan) return;
    setBusyAction("convert");
    try {
      const result = await api.post<ConvertOrdersResponse, ConvertOrdersRequest>(
        `/sourcing/purchase-plans/${plan.id}/orders`,
        { overrides },
      );
      toast.success(`Created ${result.orders.length} draft orders`);
      navigate("/orders");
    } catch {
      toast.error("Could not create draft orders");
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm text-muted">
            Projects / {project?.name ?? "Project"} / Purchase plan
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold">
              Purchase plan #{plan.id.slice(0, 8)} - {project?.name ?? "Project"} - strategy={plan.strategy}
            </h1>
            <span className={`pill ${fresh ? "bg-success/10 text-success" : "bg-warning/10 text-warning"}`}>
              {refreshedLabel(plan)}
            </span>
            {!fresh && plan.last_refreshed_at && (
              <span className="pill bg-danger/10 text-danger">Refresh stale (&gt;10 min)</span>
            )}
          </div>
        </div>
        <PoweredByTrustedParts />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
        <div className="card p-4">
          <div className="section-title">Distributors</div>
          <div className="text-2xl font-semibold">{plan.distributors_used.length}</div>
        </div>
        <div className="card p-4">
          <div className="section-title">Est. cost</div>
          <div className="text-2xl font-semibold">{formatMoney(plan.est_total_cost, currency)}</div>
        </div>
        <div className="card p-4">
          <div className="section-title">Worst lead time</div>
          <div className="text-2xl font-semibold">{formatLeadTime(plan.worst_lead_time_days)}</div>
        </div>
        <div className="card p-4">
          <div className="section-title">Unfilled</div>
          <div className="text-2xl font-semibold">{plan.unfilled_count}</div>
        </div>
      </div>

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
                tableId={`purchase-plan-${plan.id}-${distributor}`}
                exportFilename={`purchase-plan-${distributor}`}
              />
            </div>
          </details>
        );
      })}

      {unfilled.length > 0 && (
        <section className="rounded-md border border-danger/40 bg-panel p-4">
          <h2 className="text-md font-semibold text-danger">Unfilled lines</h2>
          <DataTable
            rows={unfilled}
            columns={columns}
            rowKey={line => line.id}
            tableId={`purchase-plan-${plan.id}-unfilled`}
          />
        </section>
      )}

      <div className="sticky bottom-0 bg-panel border border-border rounded-md p-3 flex flex-wrap justify-end gap-2">
        <button type="button" className="btn" onClick={refresh} disabled={busyAction !== null}>
          <RefreshCw size={14} className={busyAction === "refresh" ? "animate-spin" : ""} />
          Refresh prices
        </button>
        <button type="button" className="btn-primary" onClick={convert} disabled={!fresh || busyAction !== null}>
          <ShoppingCart size={14} />
          Create draft orders
        </button>
      </div>

      <OverrideOfferModal line={activeOverrideLine} onSelect={selectOffer} onClose={() => setOverrideLine(null)} />
    </div>
  );
}
