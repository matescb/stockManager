import { useCallback, useMemo, useRef, useState, type SetStateAction } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { RefreshCw, ShoppingCart } from "lucide-react";
import { toast } from "sonner";
import { PoweredByTrustedParts } from "@/components/PoweredByTrustedParts";
import { ApiError, api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import type { Project } from "@/types";
import OverrideOfferModal from "./OverrideOfferModal";
import PurchasePlanLinesTable from "./PurchasePlanLinesTable";
import {
  formatLeadTime,
  formatMoney,
  groupLines,
  isRefreshFresh,
  purchasePlanOverrideMatchesOffer,
  purchasePlanActionErrorMessage,
  recomputePlanFromLines,
  refreshedLabel,
  selectedQtyForOffer,
  summaryCurrency,
  unitPriceForOffer,
} from "./purchasePlanHelpers";
import type {
  ConvertOrdersRequest,
  ConvertOrdersResponse,
  PurchasePlan,
  PurchasePlanLine,
  PurchasePlanOffer,
  PurchasePlanOrderOverride,
} from "./purchasePlanTypes";
type LocationState = { plan?: PurchasePlan; project?: Project };
type OrderOverrides = Record<string, PurchasePlanOrderOverride>;

export default function PurchasePlanReviewPage() {
  const { projectId, planId } = useParams<{ projectId: string; planId: string }>();
  const navigate = useNavigate();
  const { state } = useLocation();
  const locationState = (state ?? {}) as LocationState;
  const queryClient = useQueryClient();
  const purchasePlanKey = useWsKey("purchase-plan", planId);
  const initialPlan = locationState.plan?.id === planId ? locationState.plan : undefined;
  // Set when conversion succeeds: disables the plan query BEFORE its
  // cache entry is removed. With an active observer, removeQueries
  // schedules an immediate refetch that repopulates the cache — react-
  // router 7 defers unmount past navigate(), so the observer is still
  // alive at that point (v6 merely masked the race).
  const [converted, setConverted] = useState(false);
  const planQuery = useQuery({
    queryKey: purchasePlanKey,
    queryFn: ({ signal }) => api.get<PurchasePlan>(`/projects/${projectId}/purchase-plans/${planId}`, { signal }),
    enabled: !!projectId && !!planId && !converted,
    // Also gated on `converted`: initialData re-seeds a cache entry that
    // removeQueries just deleted, even on a disabled observer.
    initialData: converted ? undefined : initialPlan,
    staleTime: 5 * 60 * 1000,
  });
  const plan = planQuery.data ?? null;
  const [project] = useState<Project | undefined>(locationState.project);
  const [busyAction, setBusyAction] = useState<"refresh" | "convert" | null>(null);
  const [refreshAttention, setRefreshAttention] = useState(false);
  const [overrideLine, setOverrideLine] = useState<PurchasePlanLine | null>(null);
  const [overrides, setOverridesState] = useState<OrderOverrides>({});
  const overridesRef = useRef(overrides);
  const setOverrides = useCallback((update: SetStateAction<OrderOverrides>) => {
    setOverridesState(current => {
      const next = typeof update === "function"
        ? (update as (current: OrderOverrides) => OrderOverrides)(current)
        : update;
      overridesRef.current = next;
      return next;
    });
  }, []);
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
  if (planQuery.isLoading) {
    return (
      <div className="card p-4">
        <div className="font-medium">Loading purchase plan...</div>
      </div>
    );
  }
  if (planQuery.isError) {
    const message = planQuery.error instanceof ApiError && planQuery.error.userMessage
      ? planQuery.error.userMessage
      : "Could not load purchase plan";
    return (
      <div className="card border-danger/40 p-4">
        <div className="font-medium text-danger">Could not load purchase plan</div>
        <div className="mt-1 text-sm text-muted">{message}</div>
        <button type="button" className="btn mt-3" onClick={() => navigate(`/projects/${projectId}/sourcing`)}>
          Back to sourcing
        </button>
      </div>
    );
  }
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
  function pruneStaleOverrides(
    current: OrderOverrides,
    refreshed: PurchasePlan,
  ): { retained: OrderOverrides; dropped: string[] } {
    const retained: OrderOverrides = {};
    const dropped: string[] = [];
    const linesById = new Map(refreshed.lines.map(line => [line.id, line]));
    for (const [lineId, override] of Object.entries(current)) {
      const refreshedLine = linesById.get(lineId);
      const stillAvailable = refreshedLine
        ? (refreshedLine.available_offers ?? []).some(offer =>
            purchasePlanOverrideMatchesOffer(refreshedLine, override, offer),
          )
        : false;
      if (stillAvailable) {
        retained[lineId] = override;
      } else {
        dropped.push(lineId);
      }
    }
    return { retained, dropped };
  }
  function planWithOverrides(
    nextPlan: PurchasePlan,
    retainedOverrides: OrderOverrides,
  ): PurchasePlan {
    const nextLines = nextPlan.lines.map(line => {
      const override = retainedOverrides[line.id];
      if (!override) return line;
      const matchingOffer = (line.available_offers ?? []).find(offer =>
        purchasePlanOverrideMatchesOffer(line, override, offer),
      );
      return {
        ...line,
        selected_distributor: override.selected_distributor,
        selected_qty: override.selected_qty,
        selected_unit_price: override.selected_unit_price,
        selected_currency: override.selected_currency,
        selected_packaging: matchingOffer?.packaging ?? null,
        selected_moq: matchingOffer?.moq ?? null,
        selected_lead_time_days: matchingOffer?.lead_time_days ?? null,
        selected_url: matchingOffer?.url ?? null,
      };
    });
    return recomputePlanFromLines(nextPlan, nextLines);
  }
  async function refresh() {
    if (!plan) return;
    setBusyAction("refresh");
    try {
      const next = await api.post<PurchasePlan>(`/sourcing/purchase-plans/${plan.id}/refresh`);
      const { retained: pruned, dropped } = pruneStaleOverrides(overridesRef.current, next);
      if (dropped.length > 0) {
        toast.info(
          `Removed ${dropped.length} override${dropped.length === 1 ? "" : "s"} no longer available after refresh.`,
        );
      }
      setOverrides(current => pruneStaleOverrides(current, next).retained);
      queryClient.setQueryData(purchasePlanKey, planWithOverrides(next, pruned));
      setRefreshAttention(false);
      toast.success("Prices refreshed");
    } catch (err) {
      if (err instanceof ApiError && err.code === "sourcing.plan_stale") setRefreshAttention(true);
      toast.error(purchasePlanActionErrorMessage(err, "Failed to refresh prices"));
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
    queryClient.setQueryData<PurchasePlan>(purchasePlanKey, current => {
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
      setConverted(true);
      queryClient.removeQueries({ queryKey: purchasePlanKey, exact: true });
      navigate("/orders");
    } catch (err) {
      if (err instanceof ApiError && err.code === "sourcing.plan_stale") setRefreshAttention(true);
      toast.error(purchasePlanActionErrorMessage(err, "Could not create draft orders"));
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
          <div className="mt-1 flex flex-wrap items-center gap-2" data-testid="purchase-plan-loaded">
            <h1 className="page-title">
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
      <PurchasePlanLinesTable
        planId={plan.id}
        grouped={grouped}
        unfilled={unfilled}
        currency={currency}
        overrides={overrides}
        onOverride={setOverrideLine}
      />
      <div className="sticky bottom-0 bg-panel border border-border rounded-md p-3 flex flex-wrap justify-end gap-2">
        <button
          type="button"
          className={refreshAttention ? "btn border-warning text-warning" : "btn"}
          onClick={refresh}
          disabled={busyAction !== null}
        >
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
