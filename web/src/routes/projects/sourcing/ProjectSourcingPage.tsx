import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { PoweredByTrustedParts } from "@/components/PoweredByTrustedParts";
import { ApiError, api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import AlertFormModal from "@/routes/sourcing/alerts/AlertFormModal";
import type { Project } from "@/types";
import { BomRows } from "./BomRows";
import { CapacityBanner } from "./CapacityBanner";
import { CoverageMatrix } from "./CoverageMatrix";
import PurchasePlanOptionsModal from "./PurchasePlanOptionsModal";
import type { PurchasePlan, PurchasePlanRequest } from "./purchasePlanTypes";
import { SourcingControls } from "./SourcingControls";
import { BudgetState, EmptyBomState, SourcingDiagnosticsPanel, SourcingSkeleton } from "./SourcingStates";
import { errorStatus, formatCount } from "./sourcingHelpers";
import { useSourcingFilters } from "./useSourcingFilters";
import { useProjectSourcing } from "./useProjectSourcing";

export default function ProjectSourcingPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const [budgetDisabledUntil, setBudgetDisabledUntil] = useState<number | null>(null);
  const [planModalOpen, setPlanModalOpen] = useState(false);
  const [alertModalOpen, setAlertModalOpen] = useState(false);
  const [planPending, setPlanPending] = useState(false);
  const {
    activeListErrors,
    buildQuantity,
    country,
    currency,
    distributors,
    filterWarnings,
    requestBody,
    setBuildQuantity,
    setCountry,
    setCurrency,
    setDistributors,
    sourceBlocked,
    workspace,
  } = useSourcingFilters(budgetDisabledUntil);
  const { data: project } = useQuery({
    queryKey: useWsKey("project", projectId),
    queryFn: ({ signal }) => api.get<Project>(`/projects/${projectId}`, { signal }),
    enabled: !!projectId,
  });

  const { sourcing, sourcingData } = useProjectSourcing({
    projectId,
    onBudgetPaused: setBudgetDisabledUntil,
  });
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
  const errorCode = sourcing.error instanceof ApiError ? sourcing.error.code : undefined;
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
            <h1 className="page-title">Source BOM</h1>
            {sourcingData?.partial && <span className="pill bg-warning/10 text-warning">Partial — some chunks served from cache</span>}
          </div>
        </div>
        <PoweredByTrustedParts primaryUrl={primaryUrl} />
      </div>

      <SourcingControls
        activeListErrors={activeListErrors}
        buildQuantity={buildQuantity}
        country={country}
        currency={currency}
        distributors={distributors}
        filterWarnings={filterWarnings}
        hasRows={hasRows}
        isSourcing={sourcing.isPending}
        projectId={projectId}
        sourceDisabled={sourceDisabled}
        workspace={workspace}
        onAlertOpen={() => setAlertModalOpen(true)}
        onBuildQuantityChange={setBuildQuantity}
        onCountryChange={setCountry}
        onCurrencyChange={setCurrency}
        onDistributorsChange={setDistributors}
        onPlanOpen={() => setPlanModalOpen(true)}
        onSource={runSourcing}
      />

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
      {sourcing.isError &&
        status !== 409 &&
        status !== 502 &&
        status !== 503 &&
        errorCode !== "sourcing.currency_mismatch" && (
        <div className="card p-4 text-sm text-danger">Failed to source BOM.</div>
      )}
      {errorCode === "sourcing.currency_mismatch" && (
        <div className="rounded-md border border-warning/40 bg-warning/10 p-4 text-sm" role="status">
          <div className="font-medium text-warning">Sourcing returned mixed currencies.</div>
          <div className="mt-1 text-muted">
            Review workspace sourcing currency settings before converting offers.
          </div>
          <Link className="mt-2 inline-block text-accent hover:underline" to="/settings/workspace">
            Open workspace settings
          </Link>
        </div>
      )}

      {sourcingData && !hasRows && <EmptyBomState projectId={projectId} />}
      <SourcingDiagnosticsPanel data={sourcingData} projectId={projectId} status={status} onRefresh={runSourcing} />
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
