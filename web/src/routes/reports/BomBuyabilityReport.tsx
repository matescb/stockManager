import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { BarChart3 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { formatMoney } from "@/lib/format";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import { PoweredByTrustedParts } from "@/components/PoweredByTrustedParts";
import { SourcingSourceLabel } from "@/components/SourcingSourceLabel";

type SourcingStatus = "ok" | "not_configured" | "partial" | "budget_blocked";

type ProjectBuyabilityRow = {
  project_id: string;
  project_name: string;
  build_quantity: number;
  can_build_now: number;
  can_build_after_purchase: number;
  blocking_lines_count: number;
  est_purchase_cost: string | number | null;
  partial: boolean;
};

type BomBuyabilityReportOut = {
  build_quantity: number;
  rows: ProjectBuyabilityRow[];
  sourcing_status: SourcingStatus;
  truncated: boolean;
  project_cap: number;
  powered_by: "TrustedParts";
  links: {
    primary: string;
    attribution: string;
  };
};

function parseQuantity(raw: string | null): number {
  const value = Number(raw ?? "1");
  if (!Number.isFinite(value) || value < 1) return 1;
  return Math.floor(value);
}

function statusCopy(status: SourcingStatus): { label: string; className: string } {
  switch (status) {
    case "ok":
      return { label: "Sourcing ok", className: "border-success/30 bg-success/10 text-success" };
    case "not_configured":
      return { label: "Sourcing not configured", className: "border-warning/30 bg-warning/10 text-warning" };
    case "partial":
      return { label: "Partial sourcing data", className: "border-warning/30 bg-warning/10 text-warning" };
    case "budget_blocked":
      return { label: "Sourcing budget blocked", className: "border-danger/30 bg-danger/10 text-danger" };
    default:
      return status satisfies never;
  }
}

export default function BomBuyabilityReport() {
  const [params, setParams] = useSearchParams();
  const qtyFromUrl = parseQuantity(params.get("build_quantity"));
  const [qty, setQty] = useState(qtyFromUrl);

  useEffect(() => {
    setQty(qtyFromUrl);
  }, [qtyFromUrl]);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: useWsKey("report", "bom-buyability", qtyFromUrl),
    queryFn: ({ signal }) => api.get<BomBuyabilityReportOut>(`/reports/bom-buyability?build_quantity=${qtyFromUrl}`, { signal }),
  });

  const status = statusCopy(data?.sourcing_status ?? "ok");
  const rows = data?.rows ?? [];

  function applyQuantity(next: number) {
    const clean = Number.isFinite(next) && next >= 1 ? Math.floor(next) : 1;
    setQty(clean);
    setParams({ build_quantity: String(clean) });
  }

  if (isError) {
    return (
      <div className="text-danger text-sm p-4">
        Failed to load BOM buyability report. {error instanceof ApiError ? error.userMessage : ""}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="card p-4 flex flex-wrap gap-3 items-end">
        <div className="w-44">
          <label className="label" htmlFor="report-buyability-qty">Build quantity</label>
          <input
            id="report-buyability-qty"
            className="input"
            type="number"
            min={1}
            value={qty}
            onChange={e => setQty(Number(e.target.value))}
            onBlur={() => applyQuantity(qty)}
            onKeyDown={e => {
              if (e.key === "Enter") applyQuantity(qty);
            }}
          />
        </div>
        <div className="flex items-center gap-2 pb-1">
          <span className={`pill border ${status.className}`}>{status.label}</span>
          {data?.truncated && (
            <span className="pill border border-warning/30 bg-warning/10 text-warning">
              Truncated to {data.project_cap} projects
            </span>
          )}
          <PoweredByTrustedParts primaryUrl={data?.links.primary} />
          <SourcingSourceLabel source="trustedparts" />
        </div>
      </div>

      {isLoading ? (
        <div className="text-muted">Loading…</div>
      ) : (
        <DataTable
          rows={rows}
          rowKey={r => r.project_id}
          tableId="report-bom-buyability"
          empty={
            <EmptyState
              icon={BarChart3}
              title="No projects"
              description="Active projects with consumable BOM lines will appear here."
            />
          }
          exportFilename="bom-buyability"
          columns={[
            {
              key: "project",
              header: "Project",
              accessor: r => r.project_name,
              render: r => <Link className="text-accent" to={`/projects/${r.project_id}/sourcing`}>{r.project_name}</Link>,
            },
            { key: "build_quantity", header: "Build qty", accessor: r => r.build_quantity, width: "90px" },
            {
              key: "can_build_now",
              header: "Can build now",
              accessor: r => r.can_build_now,
              width: "120px",
              render: r => <span className={r.can_build_now >= r.build_quantity ? "text-success tabular-nums" : "tabular-nums"}>{r.can_build_now}</span>,
            },
            {
              key: "can_build_after_purchase",
              header: "After purchase",
              accessor: r => r.can_build_after_purchase,
              width: "130px",
              render: r => <span className={r.can_build_after_purchase >= r.build_quantity ? "text-success tabular-nums" : "tabular-nums"}>{r.can_build_after_purchase}</span>,
            },
            {
              key: "blocking_lines_count",
              header: "Blocking lines",
              accessor: r => r.blocking_lines_count,
              width: "120px",
              render: r => r.blocking_lines_count > 0 ? <span className="text-danger tabular-nums">{r.blocking_lines_count}</span> : <span className="text-muted">—</span>,
            },
            {
              key: "est_purchase_cost",
              header: "Est. purchase",
              accessor: r => r.est_purchase_cost ?? "",
              width: "130px",
              render: r => r.est_purchase_cost == null ? <span className="text-muted">—</span> : <span className="tabular-nums">{formatMoney(Number(r.est_purchase_cost), null)}</span>,
            },
            {
              key: "source_bom",
              header: "Source-BOM",
              accessor: r => r.project_name,
              width: "110px",
              render: r => <Link className="text-accent" to={`/projects/${r.project_id}/sourcing?build_quantity=${r.build_quantity}`}>Open</Link>,
            },
          ]}
        />
      )}
    </div>
  );
}
