import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FolderKanban, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import EmptyState from "@/components/EmptyState";
import { DataTable, type Column } from "@/components/DataTable";
import { PoweredByTrustedParts } from "@/components/PoweredByTrustedParts";
import { SourcingSourceLabel } from "@/components/SourcingSourceLabel";
import { ApiError, api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import type { Project } from "@/types";
import type { SourcingWorkspaceSettings } from "./SourceBomButton";

type SourcingBomOffer = {
  mpn: string;
  distributor: string;
  sku?: string | null;
  stock: number;
  unit_price?: string | number | null;
  currency?: string | null;
  packaging?: string | null;
  moq?: number | null;
  lead_time_days?: number | null;
  url?: string | null;
};

type RiskFlag =
  | "single_source"
  | "no_authorized_stock"
  | "moq_overbuy"
  | "lead_time_long"
  | "preferred_distributor_unmet";

type SourcingBomLine = {
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
  };
  capacity: {
    can_build_now: number;
    can_build_after_purchase: number;
    est_purchase_cost?: string | number | null;
    blocking_lines_now: string[];
    blocking_lines_after_purchase: string[];
  };
  powered_by: "TrustedParts";
  fetched_at: string;
  partial: boolean;
  links: {
    primary: string;
    attribution: string;
  };
};

type SourcingRequest = {
  build_quantity: number;
  country?: string;
  currency?: string;
  distributors?: string[];
};

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

function formatLeadTime(days: number | null | undefined): string {
  if (days == null) return "—";
  return days === 1 ? "1 day" : `${days.toLocaleString()} days`;
}

function splitDistributors(value: string): string[] {
  return value
    .split(",")
    .map(item => item.trim())
    .filter(Boolean);
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
  }
}

function errorStatus(error: unknown): number | null {
  return error instanceof ApiError ? error.status : null;
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

function NotConfiguredState() {
  return (
    <div className="card p-4 space-y-3" role="status">
      <div className="font-medium">Sourcing not configured.</div>
      <div className="text-sm text-muted">
        Ask a workspace admin to configure TrustedParts in Settings → Sourcing.
      </div>
      <Link className="btn" to="/settings/workspace">
        Open Settings → Sourcing
      </Link>
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

function CapacityBanner({ data }: { data: SourcingBomResponse }) {
  const blocking = data.capacity.blocking_lines_after_purchase.length > 0
    ? data.capacity.blocking_lines_after_purchase
    : data.capacity.blocking_lines_now;
  const linesById = new Map(data.rows.map(row => [row.project_entry_id, row]));
  const purchaseCurrency = data.rows.find(row => row.best_offer?.currency)?.best_offer?.currency ?? null;

  return (
    <div className="card p-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div>
          <div className="section-title">Can build now</div>
          <div className="text-2xl font-semibold tabular-nums">{data.capacity.can_build_now}</div>
        </div>
        <div>
          <div className="section-title">After purchase</div>
          <div className="text-2xl font-semibold tabular-nums">{data.capacity.can_build_after_purchase}</div>
        </div>
        <div>
          <div className="section-title">Est. cost</div>
          <div className="text-2xl font-semibold tabular-nums">{formatMoney(data.capacity.est_purchase_cost, purchaseCurrency)}</div>
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

function CoverageMatrix({ data }: { data: SourcingBomResponse }) {
  const bestSingle = data.coverage.best_single_distributor;
  const bestTwo: string[] = data.coverage.best_two_distributor_combo ?? [];
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
      render: row => formatMoney(row.est_total_cost),
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

function BomRows({ rows }: { rows: SourcingBomLine[] }) {
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
      accessor: row => numberOrNull(row.best_offer?.unit_price),
      render: row => row.best_offer
        ? formatMoney(row.best_offer.unit_price, row.best_offer.currency)
        : <span className="text-muted">—</span>,
      align: "right",
    },
    {
      key: "distributor",
      header: "Distributor",
      accessor: row => row.best_offer?.distributor ?? "",
      render: row => row.best_offer?.url ? (
        <a className="text-accent hover:underline" href={row.best_offer.url} target="_blank" rel="noopener noreferrer">
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
      key: "risk",
      header: "Risk",
      accessor: row => row.risk_flags.join(" "),
      render: row => (
        <span className="flex flex-wrap gap-1">
          {row.risk_flags.length === 0 ? (
            <span className="text-muted">—</span>
          ) : row.risk_flags.map(flag => (
            <span key={flag} className="pill bg-warning/10 text-warning">
              {riskLabel(flag)}
            </span>
          ))}
        </span>
      ),
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
        tableId="project-sourcing-bom"
        exportFilename="sourced-bom"
      />
    </section>
  );
}

export default function ProjectSourcingPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [buildQuantity, setBuildQuantity] = useState(1);
  const [country, setCountry] = useState("");
  const [currency, setCurrency] = useState("");
  const [distributors, setDistributors] = useState("");
  const [defaultsApplied, setDefaultsApplied] = useState(false);
  const [budgetDisabledUntil, setBudgetDisabledUntil] = useState<number | null>(null);

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
    setCountry(workspace.sourcing_country_code ?? "");
    setCurrency(workspace.sourcing_currency_code ?? "");
    setDistributors((workspace.sourcing_preferred_distributors ?? []).join(", "));
    setDefaultsApplied(true);
  }, [workspace, defaultsApplied]);

  const requestBody = useMemo<SourcingRequest>(() => {
    const body: SourcingRequest = { build_quantity: Math.max(1, Math.floor(buildQuantity || 1)) };
    const cleanCountry = country.trim().toUpperCase();
    const cleanCurrency = currency.trim().toUpperCase();
    const cleanDistributors = splitDistributors(distributors);
    if (cleanCountry) body.country = cleanCountry;
    if (cleanCurrency) body.currency = cleanCurrency;
    if (cleanDistributors.length > 0) body.distributors = cleanDistributors;
    return body;
  }, [buildQuantity, country, currency, distributors]);

  const query = useQuery({
    queryKey: useWsKey("sourcing", "project", projectId, requestBody),
    queryFn: ({ signal }) =>
      api.post<SourcingBomResponse, SourcingRequest>(`/projects/${projectId}/sourcing`, requestBody, { signal }),
    enabled: !!projectId && buildQuantity >= 1 && defaultsApplied,
  });
  const queryIsError = query.isError;
  const queryError = query.error;
  const refetchSourcing = query.refetch;

  useEffect(() => {
    if (!queryIsError) return;
    const status = errorStatus(queryError);
    if (status === 503) setBudgetDisabledUntil(Date.now() + 5 * 60 * 1000);
    if (status === 502 || (queryError instanceof Error && queryError.name === "AbortError")) {
      toast.error("TrustedParts unavailable. Retry?", {
        action: { label: "Retry", onClick: () => refetchSourcing() },
      });
    }
  }, [queryIsError, queryError, refetchSourcing]);

  useEffect(() => {
    if (budgetDisabledUntil == null) return;
    const delay = Math.max(0, budgetDisabledUntil - Date.now());
    const timeout = window.setTimeout(() => setBudgetDisabledUntil(null), delay);
    return () => window.clearTimeout(timeout);
  }, [budgetDisabledUntil]);

  const status = errorStatus(query.error);
  const sourceDisabled = query.isFetching || buildQuantity < 1 || (budgetDisabledUntil != null && Date.now() < budgetDisabledUntil);
  const hasRows = (query.data?.rows.length ?? 0) > 0;
  const primaryUrl = query.data?.links.primary;

  if (!projectId) return null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm text-muted">Projects · {project?.name ?? "Project"} · Sourcing</div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold">Source BOM</h1>
            {query.data?.partial && <span className="pill bg-warning/10 text-warning">Partial — some chunks served from cache</span>}
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
            <input
              id="sourcing-country"
              className="input uppercase"
              maxLength={2}
              value={country}
              onChange={event => setCountry(event.target.value.toUpperCase())}
              placeholder="US"
            />
          </div>
          <div>
            <label className="label" htmlFor="sourcing-currency">Currency</label>
            <input
              id="sourcing-currency"
              className="input uppercase"
              maxLength={3}
              value={currency}
              onChange={event => setCurrency(event.target.value.toUpperCase())}
              placeholder="USD"
            />
          </div>
          <div className="md:col-span-2">
            <label className="label" htmlFor="sourcing-distributors">Distributors</label>
            <input
              id="sourcing-distributors"
              className="input"
              value={distributors}
              onChange={event => setDistributors(event.target.value)}
              placeholder="DigiKey, Mouser"
            />
          </div>
        </div>
        <div className="mt-3 flex justify-end">
          <button
            type="button"
            className="btn-primary"
            disabled={sourceDisabled}
            onClick={() => query.refetch()}
          >
            <RefreshCw size={14} className={query.isFetching ? "animate-spin" : ""} />
            {query.isFetching ? "Sourcing…" : "Source"}
          </button>
        </div>
      </div>

      {query.isLoading && <SourcingSkeleton />}
      {status === 409 && <NotConfiguredState />}
      {status === 503 && <BudgetState disabledUntil={budgetDisabledUntil} onRetry={() => query.refetch()} />}
      {status === 502 && (
        <div className="card p-4" role="status">
          <button type="button" className="btn" onClick={() => query.refetch()}>
            Retry Source BOM
          </button>
        </div>
      )}
      {query.isError && status !== 409 && status !== 502 && status !== 503 && (
        <div className="card p-4 text-sm text-danger">Failed to source BOM.</div>
      )}

      {query.data && !hasRows && <EmptyBomState projectId={projectId} />}

      {query.data && hasRows && (
        <>
          <CapacityBanner data={query.data} />
          <CoverageMatrix data={query.data} />
          <BomRows rows={query.data.rows} />
          <div className="text-xs text-muted">
            {formatCount(query.data.rows.length)} line{query.data.rows.length === 1 ? "" : "s"} fetched from {query.data.powered_by}.
          </div>
        </>
      )}
    </div>
  );
}
