import { useState } from "react";
import { Link, NavLink, Outlet, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { BarChart3, ShoppingCart } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { formatDate, formatMoney } from "@/lib/format";
import { DataTable, quantityColumn, type Column } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import { PoweredByTrustedParts } from "@/components/PoweredByTrustedParts";
import { SourcingSourceLabel } from "@/components/SourcingSourceLabel";
import {
  CreateOrderLineModal,
  type CreateOrderLineSource,
} from "@/routes/parts/detail/CreateOrderLineModal";
import type { Order, Project } from "@/types";

type LowStockRow = {
  part_id: string;
  name: string;
  manufacturer: string | null;
  mpn: string | null;
  on_hand: number;
  reserved: number;
  available: number;
  threshold: number;
  short_by: number;
  sourcing?: LowStockSourcing | null;
};

type LowStockSourcingOffer = {
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

type LowStockSourcing = {
  authorized_stock: number;
  offers: LowStockSourcingOffer[];
  best_offer?: LowStockSourcingOffer | null;
  est_replenishment_cost?: string | number | null;
  lead_time_days?: number | null;
  preferred_distributor_available: boolean;
  cache_hit: boolean;
  fetched_at: string;
};

type LowStockSourcingStatus = "ok" | "not_configured" | "partial" | "budget_blocked";

type LowStockWithSourcing = {
  rows: LowStockRow[];
  sourcing_status: LowStockSourcingStatus;
  powered_by?: "TrustedParts" | null;
  links?: {
    primary: string;
    attribution: string;
  } | null;
};

type StockValue = {
  by_currency: { currency: string | null; value: number }[];
  by_part: { part_id: string; name: string; on_hand: number; value: number; currency: string | null }[];
};

type Shortage = {
  project_id: string;
  quantity: number;
  total_short: number;
  rows: { part_id: string; part_name: string; required: number; available: number; short_by: number }[];
};

type Expiring = {
  lot_id: string;
  name: string | null;
  part_id: string;
  part_name: string | null;
  on_hand: number;
  expiration_date: string;
  days_until_expiry: number;
  expired: boolean;
}[];

/**
 * Turn a list of (part, quantity) shortages into a fresh purchase order
 * pre-populated with one entry per part. Used by the action button on
 * Low-stock and BOM-shortage so a report finding becomes a one-click
 * restock instead of busywork in OrderDetail.
 *
 * Entries POST in parallel — partial failures are reported as a warning;
 * the order itself still exists with whatever succeeded so the user can
 * pick up from OrderDetail.
 */
async function createRestockOrder(
  name: string,
  lines: { part_id: string; quantity: number }[],
  nav: ReturnType<typeof useNavigate>,
  qc: QueryClient,
  workspaceId: string | null,
): Promise<void> {
  if (lines.length === 0) return;
  try {
    const order = await api.post<Order>("/orders", { name });
    const settled = await Promise.allSettled(
      lines.map(l =>
        api.post(`/orders/${order.id}/entries`, {
          part_id: l.part_id,
          quantity_ordered: l.quantity,
        })
      )
    );
    const failed = settled.filter(s => s.status === "rejected").length;
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "orders") });
    if (failed > 0) {
      toast.warning(
        `Order created — ${lines.length - failed} of ${lines.length} lines added; ${failed} failed.`
      );
    } else {
      toast.success(`Order with ${lines.length} line${lines.length === 1 ? "" : "s"} created.`);
    }
    nav(`/orders/${order.id}`);
  } catch (e) {
    toast.error(e instanceof ApiError ? e.userMessage : "Failed to create order");
  }
}

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

function numberOrNull(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function formatSourcingMoney(value: string | number | null | undefined, currency?: string | null): string {
  const numeric = numberOrNull(value);
  if (numeric == null) return "—";
  return formatMoney(numeric, currency);
}

function formatLeadTime(days: number | null | undefined): string {
  if (days == null) return "—";
  return days === 1 ? "1 day" : `${days.toLocaleString()} days`;
}

function createOrderLineSource(row: LowStockRow): CreateOrderLineSource | null {
  const offer = row.sourcing?.best_offer;
  if (!row.sourcing || !offer) return null;
  return {
    partId: row.part_id,
    distributor: offer.distributor,
    packaging: offer.packaging ?? null,
    leadTimeDays: offer.lead_time_days ?? null,
    fetchedAt: row.sourcing.fetched_at ?? null,
    quantity: Math.max(1, row.short_by, offer.moq ?? 1),
    unitPrice: numberOrNull(offer.unit_price),
    currency: offer.currency ?? null,
    productUrl: offer.url ?? null,
  };
}

export default function ReportsLayout() {
  return (
    <div className="space-y-4">
      <KpiStrip />
      <div className="flex items-center gap-1">
        <NavLink end to="/reports" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Low stock</NavLink>
        <NavLink to="/reports/value" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Stock value</NavLink>
        <NavLink to="/reports/replenishment-cost" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Replenishment cost</NavLink>
        <NavLink to="/reports/bom" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>BOM shortage</NavLink>
        <NavLink to="/reports/buyability" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>BOM buyability</NavLink>
        <NavLink to="/reports/expiring" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Expiring lots</NavLink>
        <NavLink to="/reports/sourcing-risk" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Sourcing risk</NavLink>
      </div>
      <Outlet />
    </div>
  );
}

type KpiTone = "default" | "danger" | "warning" | "success";

function KpiCard({
  label,
  value,
  hint,
  to,
  tone = "default",
}: {
  label: string;
  value: string | number;
  hint?: string;
  to: string;
  tone?: KpiTone;
}) {
  const toneCls =
    tone === "danger"
      ? "text-danger"
      : tone === "warning"
        ? "text-warning"
        : tone === "success"
          ? "text-success"
          : "text-text";
  return (
    <Link
      to={to}
      className="card p-4 flex flex-col gap-1 hover:bg-panel2/60 transition-colors"
    >
      <div className="section-title">{label}</div>
      <div className={`text-2xl font-semibold tabular-nums ${toneCls}`}>{value}</div>
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </Link>
  );
}

function KpiStrip() {
  const { data: lowStock } = useQuery({
    queryKey: useWsKey("report", "low-stock"),
    queryFn: ({ signal }) => api.get<LowStockRow[]>("/reports/low-stock", { signal }),
  });
  const { data: stockValue } = useQuery({
    queryKey: useWsKey("report", "stock-value"),
    queryFn: ({ signal }) => api.get<StockValue>("/reports/stock-value", { signal }),
  });
  const { data: expiring } = useQuery({
    queryKey: useWsKey("report", "expiring", 30),
    queryFn: ({ signal }) => api.get<Expiring>("/reports/expiring-lots?days=30", { signal }),
  });

  const lowCount = lowStock?.length ?? 0;
  const valueByCurrency = stockValue?.by_currency
    ?.filter(c => c.value > 0)
    .map(c => formatMoney(c.value, c.currency))
    .join(" · ") ?? "—";
  const expiringCount = expiring?.length ?? 0;
  const expiredCount = expiring?.filter(l => l.expired).length ?? 0;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
      <KpiCard
        label="Low-stock items"
        value={lowCount}
        hint={lowCount === 0 ? "All threshold-tagged parts are stocked." : "below their report threshold"}
        to="/reports"
        tone={lowCount > 0 ? "danger" : "success"}
      />
      <KpiCard
        label="Stock value"
        value={valueByCurrency}
        hint={stockValue?.by_part?.length ? `across ${stockValue.by_part.length} part${stockValue.by_part.length === 1 ? "" : "s"}` : "no purchase-cost-tagged stock"}
        to="/reports/value"
      />
      <KpiCard
        label="Expiring < 30 days"
        value={expiringCount}
        hint={
          expiredCount > 0
            ? `${expiredCount} already expired`
            : expiringCount === 0
              ? "no lots expire in the next 30 days"
              : "lots with on-hand stock"
        }
        to="/reports/expiring"
        tone={expiredCount > 0 ? "danger" : expiringCount > 0 ? "warning" : "success"}
      />
    </div>
  );
}

export function LowStockReport() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const includeSourcing = searchParams.get("include_sourcing") === "true";
  const [orderLineSource, setOrderLineSource] = useState<CreateOrderLineSource | null>(null);
  const { data, isLoading, isError, error } = useQuery<LowStockRow[] | LowStockWithSourcing>({
    queryKey: useWsKey("report", "low-stock", includeSourcing),
    queryFn: ({ signal }) =>
      includeSourcing
        ? api.get<LowStockWithSourcing>("/reports/low-stock?include_sourcing=true", { signal })
        : api.get<LowStockRow[]>("/reports/low-stock", { signal }),
  });

  const restockMutation = useApiMutation<void, { name: string; lines: { part_id: string; quantity: number }[] }>({
    mutationKey: ["report", "low-stock", "restock"],
    mutationFn: async ({ name, lines }) => {
      await createRestockOrder(name, lines, nav, qc, workspaceId);
    },
  });


  if (isError) return <div className="text-danger text-sm p-4">Failed to load low-stock report. {error instanceof ApiError ? error.userMessage : ""}</div>;
  if (isLoading) return <div className="text-muted">Loading…</div>;
  const sourcedData = includeSourcing && data && !Array.isArray(data) ? data : null;
  const rows = Array.isArray(data) ? data : data?.rows ?? [];
  const busy = restockMutation.isPending;
  const sourcingStatus = sourcedData?.sourcing_status;

  function setIncludeSourcing(next: boolean) {
    const params = new URLSearchParams(searchParams);
    if (next) params.set("include_sourcing", "true");
    else params.delete("include_sourcing");
    setSearchParams(params, { replace: false });
  }

  function orderShortages() {
    if (busy || rows.length === 0) return;
    restockMutation.mutate({
      name: `Restock ${todayISO()}`,
      lines: rows.map(r => ({ part_id: r.part_id, quantity: Math.max(1, r.short_by) })),
    });
  }

  const columns: Column<LowStockRow>[] = [
    { key: "name", header: "Part", accessor: r => r.name, render: r => <Link className="text-accent" to={`/parts/${r.part_id}/info`}>{r.name}</Link> },
    { key: "mpn", header: "MPN", accessor: r => r.mpn ?? "" },
    { key: "manufacturer", header: "Manufacturer", accessor: r => r.manufacturer ?? "" },
    quantityColumn<LowStockRow>({ key: "on_hand", header: "On hand", value: r => r.on_hand, width: "80px" }),
    quantityColumn<LowStockRow>({ key: "reserved", header: "Reserved", value: r => r.reserved ?? 0, width: "90px",
      render: text => <span className="tabular-nums text-muted">{text}</span> }),
    quantityColumn<LowStockRow>({ key: "available", header: "Available", value: r => r.available ?? r.on_hand, width: "90px" }),
    quantityColumn<LowStockRow>({ key: "threshold", header: "Threshold", value: r => r.threshold, width: "100px" }),
    quantityColumn<LowStockRow>({ key: "short_by", header: "Short by", value: r => r.short_by, width: "100px",
      render: text => <span className="tabular-nums text-danger">{text}</span> }),
    ...(includeSourcing ? [
      {
        key: "authorized_stock",
        header: "Authorized stock",
        accessor: (r: LowStockRow) => r.sourcing?.authorized_stock ?? null,
        render: (r: LowStockRow) => r.sourcing ? <span className="tabular-nums">{r.sourcing.authorized_stock}</span> : <span className="text-muted">—</span>,
        align: "right" as const,
      },
      {
        key: "best_offer",
        header: "Best offer",
        accessor: (r: LowStockRow) => numberOrNull(r.sourcing?.best_offer?.unit_price),
        render: (r: LowStockRow) => r.sourcing?.best_offer
          ? formatSourcingMoney(r.sourcing.best_offer.unit_price, r.sourcing.best_offer.currency)
          : <span className="text-muted">—</span>,
        align: "right" as const,
      },
      {
        key: "moq",
        header: "MOQ",
        accessor: (r: LowStockRow) => r.sourcing?.best_offer?.moq ?? null,
        render: (r: LowStockRow) => r.sourcing?.best_offer?.moq ?? <span className="text-muted">—</span>,
        align: "right" as const,
      },
      {
        key: "lead_time",
        header: "Lead time",
        accessor: (r: LowStockRow) => r.sourcing?.lead_time_days ?? null,
        render: (r: LowStockRow) => formatLeadTime(r.sourcing?.lead_time_days),
        align: "right" as const,
      },
      {
        key: "preferred",
        header: "Preferred?",
        accessor: (r: LowStockRow) => r.sourcing?.preferred_distributor_available ?? false,
        render: (r: LowStockRow) => r.sourcing?.preferred_distributor_available ? "✓" : <span className="text-muted">—</span>,
        align: "center" as const,
      },
      {
        key: "source",
        header: "Source",
        accessor: () => "TrustedParts",
        render: () => <SourcingSourceLabel source="trustedparts" />,
      },
      {
        key: "draft_po",
        header: "Draft PO",
        accessor: (r: LowStockRow) => r.sourcing?.best_offer?.distributor ?? "",
        render: (r: LowStockRow) => {
          const source = createOrderLineSource(r);
          return (
            <button
              type="button"
              className="btn-sm"
              disabled={!source}
              onClick={() => setOrderLineSource(source)}
            >
              Create draft PO
            </button>
          );
        },
      },
    ] satisfies Column<LowStockRow>[] : []),
  ];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="inline-flex items-center gap-2 text-sm text-text" htmlFor="low-stock-include-sourcing">
          <input
            id="low-stock-include-sourcing"
            type="checkbox"
            checked={includeSourcing}
            onChange={event => setIncludeSourcing(event.currentTarget.checked)}
          />
          Include sourcing data
        </label>
        {includeSourcing && (
          <div className="flex flex-wrap items-center gap-2">
            <PoweredByTrustedParts primaryUrl={sourcedData?.links?.primary} />
            <SourcingSourceLabel source="trustedparts" />
          </div>
        )}
        {rows.length > 0 && (
          <button
            type="button"
            className="btn-primary inline-flex items-center gap-1.5"
            disabled={busy}
            onClick={orderShortages}
          >
            <ShoppingCart size={14} />
            Create restock order ({rows.length})
          </button>
        )}
      </div>
      {includeSourcing && sourcingStatus === "not_configured" && (
        <div className="card p-3 text-sm text-muted" role="status">Sourcing not configured.</div>
      )}
      {includeSourcing && sourcingStatus === "partial" && (
        <div className="card p-3 text-sm text-warning" role="status">Partial — some rows from cache.</div>
      )}
      {includeSourcing && sourcingStatus === "budget_blocked" && (
        <div className="card p-3 text-sm text-warning" role="status">Budget exhausted — sourcing data omitted for some rows.</div>
      )}
    <DataTable
      rows={rows}
      rowKey={r => r.part_id}
      tableId="report-low-stock"
      empty={
        <EmptyState
          icon={BarChart3}
          title="All clear"
          description="All threshold-tagged parts are stocked."
        />
      }
      exportFilename="low-stock"
      columns={columns}
    />
    <CreateOrderLineModal
      open={orderLineSource !== null}
      source={orderLineSource}
      onClose={() => setOrderLineSource(null)}
    />
    </div>
  );
}

export function StockValueReport() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: useWsKey("report", "stock-value"),
    queryFn: ({ signal }) => api.get<StockValue>("/reports/stock-value", { signal }),
  });
  if (isError) return <div className="text-danger text-sm p-4">Failed to load stock value report. {error instanceof ApiError ? error.userMessage : ""}</div>;
  if (isLoading) return <div className="text-muted">Loading…</div>;
  if (!data) return null;
  return (
    <div className="space-y-4">
      <div className="card p-4">
        <h3 className="card-title mb-2">By currency</h3>
        <table className="table">
          <thead><tr><th>Currency</th><th>Total value</th></tr></thead>
          <tbody>
            {data.by_currency.map(c => (
              <tr key={c.currency ?? "none"}>
                <td>{c.currency ?? <span className="text-muted">— (untagged)</span>}</td>
                <td className="tabular-nums">{formatMoney(c.value, c.currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <DataTable
        rows={data.by_part}
        rowKey={r => r.part_id}
        tableId="report-stock-value-by-part"
        empty={
          <EmptyState
            icon={BarChart3}
            title="No data"
            description="No purchase-cost-tagged stock yet. Add stock with a price to populate this report."
          />
        }
        exportFilename="stock-value-by-part"
        columns={[
          { key: "name", header: "Part", accessor: r => r.name, render: r => <Link className="text-accent" to={`/parts/${r.part_id}/info`}>{r.name}</Link> },
          quantityColumn<StockValue["by_part"][number]>({ key: "on_hand", header: "On hand", value: r => r.on_hand, width: "80px" }),
          { key: "value", header: "Value", accessor: r => r.value, render: r => <span className="tabular-nums">{formatMoney(r.value, r.currency)}</span> },
          { key: "currency", header: "Currency", accessor: r => r.currency ?? "—", width: "100px" },
        ]}
      />
    </div>
  );
}

export function BomShortageReport() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const { data: projects } = useQuery({ queryKey: useWsKey("projects"), queryFn: ({ signal }) => api.get<Project[]>("/projects", { signal }) });
  const [projectId, setProjectId] = useState("");
  const [qty, setQty] = useState(1);
  const { data } = useQuery({
    queryKey: useWsKey("report", "bom", projectId, qty),
    queryFn: ({ signal }) => api.get<Shortage>(`/reports/bom-shortage?project_id=${projectId}&quantity=${qty}`, { signal }),
    enabled: !!projectId && qty > 0,
  });

  const shortages = data?.rows.filter(r => r.short_by > 0) ?? [];
  const projectName = projects?.find(p => p.id === projectId)?.name;

  const restockMutation = useApiMutation<void, { name: string; lines: { part_id: string; quantity: number }[] }>({
    mutationKey: ["report", "bom", "restock"],
    mutationFn: async ({ name, lines }) => {
      await createRestockOrder(name, lines, nav, qc, workspaceId);
    },
  });

  const busy = restockMutation.isPending;

  function orderShortages() {
    if (busy || shortages.length === 0) return;
    const subject = projectName ? `${projectName} × ${qty}` : `build × ${qty}`;
    restockMutation.mutate({
      name: `BOM restock — ${subject} (${todayISO()})`,
      lines: shortages.map(r => ({ part_id: r.part_id, quantity: r.short_by })),
    });
  }

  return (
    <div className="space-y-3">
      <div className="card p-4 flex gap-3 items-end max-w-2xl">
        <div className="flex-1">
          <label className="label" htmlFor="report-bom-project">Project</label>
          <select id="report-bom-project" className="input" value={projectId} onChange={e => setProjectId(e.target.value)}>
            <option value="">— pick —</option>
            {projects?.filter(p => !p.archived_at).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="report-bom-qty">Build quantity</label>
          <input id="report-bom-qty" className="input" type="number" min={1} value={qty} onChange={e => setQty(Number(e.target.value))} />
        </div>
      </div>
      {data && (
        <>
        {shortages.length > 0 && (
          <div className="flex justify-end">
            <button
              type="button"
              className="btn-primary inline-flex items-center gap-1.5"
              disabled={busy}
              onClick={orderShortages}
            >
              <ShoppingCart size={14} />
              Order shortages ({shortages.length})
            </button>
          </div>
        )}
        <DataTable
          rows={data.rows}
          rowKey={r => r.part_id}
          tableId="report-bom-shortage"
          empty={
            <EmptyState
              icon={BarChart3}
              title="No data"
              description="No consumable BOM lines."
            />
          }
          exportFilename="bom-shortage"
          columns={[
            { key: "name", header: "Part", accessor: r => r.part_name, render: r => <Link className="text-accent" to={`/parts/${r.part_id}/info`}>{r.part_name}</Link> },
            quantityColumn<Shortage["rows"][number]>({ key: "required", header: "Required", value: r => r.required, width: "100px" }),
            quantityColumn<Shortage["rows"][number]>({ key: "available", header: "On hand", value: r => r.available, width: "100px" }),
            quantityColumn<Shortage["rows"][number]>({ key: "short_by", header: "Short by", value: r => r.short_by, width: "100px",
              render: (text, r) => r.short_by ? <span className="tabular-nums text-danger">{text}</span> : <span className="text-muted">—</span> }),
          ]}
        />
        </>
      )}
    </div>
  );
}

export function ExpiringLotsReport() {
  const [days, setDays] = useState(90);
  const { data } = useQuery({
    queryKey: useWsKey("report", "expiring", days),
    queryFn: ({ signal }) => api.get<Expiring>(`/reports/expiring-lots?days=${days}`, { signal }),
  });
  return (
    <div className="space-y-3">
      <div className="card p-4 flex gap-3 items-end max-w-md">
        <div className="flex-1">
          <label className="label" htmlFor="report-expiring-days">Window (days)</label>
          <input id="report-expiring-days" className="input" type="number" min={0} max={3650} value={days} onChange={e => setDays(Number(e.target.value))} />
        </div>
      </div>
      <DataTable
        rows={data ?? []}
        rowKey={r => r.lot_id}
        tableId="report-expiring-lots"
        empty={
          <EmptyState
            icon={BarChart3}
            title="All clear"
            description="No lots expiring in this window."
          />
        }
        exportFilename="expiring-lots"
        columns={[
          { key: "lot", header: "Lot", accessor: r => r.name ?? r.lot_id, render: r => <Link className="text-accent" to={`/lots/${r.lot_id}/info`}>{r.name ?? r.lot_id}</Link> },
          { key: "part", header: "Part", accessor: r => r.part_name ?? r.part_id, render: r => <Link to={`/parts/${r.part_id}/info`}>{r.part_name ?? r.part_id}</Link> },
          quantityColumn<Expiring[number]>({ key: "qty", header: "On hand", value: r => r.on_hand, width: "80px" }),
          { key: "exp", header: "Expires", accessor: r => r.expiration_date, render: r => formatDate(r.expiration_date) },
          { key: "left", header: "Days", accessor: r => r.days_until_expiry, width: "80px",
            render: r => r.expired ? <span className="text-danger">expired</span> : <span className={r.days_until_expiry < 30 ? "text-warning" : ""}>{r.days_until_expiry}</span> },
        ]}
      />
    </div>
  );
}
