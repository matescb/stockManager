import { useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { BarChart3, ShoppingCart } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { wsKey, wsKeyOf } from "@/lib/queryKeys";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
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
    toast.error(e instanceof ApiError ? e.message : "Failed to create order");
  }
}

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function ReportsLayout() {
  return (
    <div className="space-y-4">
      <KpiStrip />
      <div className="flex items-center gap-1">
        <NavLink end to="/reports" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Low stock</NavLink>
        <NavLink to="/reports/value" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Stock value</NavLink>
        <NavLink to="/reports/bom" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>BOM shortage</NavLink>
        <NavLink to="/reports/expiring" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Expiring lots</NavLink>
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
    queryKey: wsKey("report", "low-stock"),
    queryFn: () => api.get<LowStockRow[]>("/reports/low-stock"),
  });
  const { data: stockValue } = useQuery({
    queryKey: wsKey("report", "stock-value"),
    queryFn: () => api.get<StockValue>("/reports/stock-value"),
  });
  const { data: expiring } = useQuery({
    queryKey: wsKey("report", "expiring", 30),
    queryFn: () => api.get<Expiring>("/reports/expiring-lots?days=30"),
  });

  const lowCount = lowStock?.length ?? 0;
  const valueByCurrency = stockValue?.by_currency
    ?.filter(c => c.value > 0)
    .map(c => `${c.value.toFixed(2)} ${c.currency ?? "?"}`)
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
  const [busy, setBusy] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: wsKey("report", "low-stock"),
    queryFn: () => api.get<LowStockRow[]>("/reports/low-stock"),
  });
  if (isLoading) return <div className="text-muted">Loading…</div>;
  const rows = data ?? [];

  async function orderShortages() {
    if (busy || rows.length === 0) return;
    setBusy(true);
    try {
      await createRestockOrder(
        `Restock ${todayISO()}`,
        rows.map(r => ({ part_id: r.part_id, quantity: Math.max(1, r.short_by) })),
        nav,
        qc,
        workspaceId,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      {rows.length > 0 && (
        <div className="flex justify-end">
          <button
            type="button"
            className="btn-primary inline-flex items-center gap-1.5"
            disabled={busy}
            onClick={orderShortages}
          >
            <ShoppingCart size={14} />
            Create restock order ({rows.length})
          </button>
        </div>
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
      columns={[
        { key: "name", header: "Part", accessor: r => r.name, render: r => <Link className="text-accent" to={`/parts/${r.part_id}/info`}>{r.name}</Link> },
        { key: "mpn", header: "MPN", accessor: r => r.mpn ?? "" },
        { key: "manufacturer", header: "Manufacturer", accessor: r => r.manufacturer ?? "" },
        { key: "on_hand", header: "On hand", accessor: r => r.on_hand, width: "80px" },
        { key: "reserved", header: "Reserved", accessor: r => r.reserved ?? 0, width: "90px",
          render: r => <span className="tabular-nums text-muted">{r.reserved ?? 0}</span> },
        { key: "available", header: "Available", accessor: r => r.available ?? r.on_hand, width: "90px" },
        { key: "threshold", header: "Threshold", accessor: r => r.threshold, width: "100px" },
        { key: "short_by", header: "Short by", accessor: r => r.short_by, width: "100px",
          render: r => <span className="tabular-nums text-danger">{r.short_by}</span> },
      ]}
    />
    </div>
  );
}

export function StockValueReport() {
  const { data, isLoading } = useQuery({
    queryKey: wsKey("report", "stock-value"),
    queryFn: () => api.get<StockValue>("/reports/stock-value"),
  });
  if (isLoading) return <div className="text-muted">Loading…</div>;
  if (!data) return null;
  return (
    <div className="space-y-4">
      <div className="card p-4">
        <h3 className="text-md font-semibold mb-3">By currency</h3>
        <table className="table">
          <thead><tr><th>Currency</th><th>Total value</th></tr></thead>
          <tbody>
            {data.by_currency.map(c => (
              <tr key={c.currency ?? "none"}>
                <td>{c.currency ?? <span className="text-muted">— (untagged)</span>}</td>
                <td className="tabular-nums">{c.value.toFixed(2)}</td>
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
          { key: "on_hand", header: "On hand", accessor: r => r.on_hand, width: "80px" },
          { key: "value", header: "Value", accessor: r => r.value, render: r => <span className="tabular-nums">{r.value.toFixed(2)}</span> },
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
  const { data: projects } = useQuery({ queryKey: wsKey("projects"), queryFn: () => api.get<Project[]>("/projects") });
  const [projectId, setProjectId] = useState("");
  const [qty, setQty] = useState(1);
  const [busy, setBusy] = useState(false);
  const { data } = useQuery({
    queryKey: wsKey("report", "bom", projectId, qty),
    queryFn: () => api.get<Shortage>(`/reports/bom-shortage?project_id=${projectId}&quantity=${qty}`),
    enabled: !!projectId && qty > 0,
  });

  const shortages = data?.rows.filter(r => r.short_by > 0) ?? [];
  const projectName = projects?.find(p => p.id === projectId)?.name;

  async function orderShortages() {
    if (busy || shortages.length === 0) return;
    setBusy(true);
    try {
      const subject = projectName ? `${projectName} × ${qty}` : `build × ${qty}`;
      await createRestockOrder(
        `BOM restock — ${subject} (${todayISO()})`,
        shortages.map(r => ({ part_id: r.part_id, quantity: r.short_by })),
        nav,
        qc,
        workspaceId,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="card p-4 flex gap-3 items-end max-w-2xl">
        <div className="flex-1">
          <label className="label">Project</label>
          <select className="input" value={projectId} onChange={e => setProjectId(e.target.value)}>
            <option value="">— pick —</option>
            {projects?.filter(p => !p.archived_at).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <div>
          <label className="label">Build quantity</label>
          <input className="input" type="number" min={1} value={qty} onChange={e => setQty(Number(e.target.value))} />
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
            { key: "required", header: "Required", accessor: r => r.required, width: "100px" },
            { key: "available", header: "On hand", accessor: r => r.available, width: "100px" },
            { key: "short_by", header: "Short by", accessor: r => r.short_by, width: "100px",
              render: r => r.short_by ? <span className="tabular-nums text-danger">{r.short_by}</span> : <span className="text-muted">—</span> },
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
    queryKey: wsKey("report", "expiring", days),
    queryFn: () => api.get<Expiring>(`/reports/expiring-lots?days=${days}`),
  });
  return (
    <div className="space-y-3">
      <div className="card p-4 flex gap-3 items-end max-w-md">
        <div className="flex-1">
          <label className="label">Window (days)</label>
          <input className="input" type="number" min={0} max={3650} value={days} onChange={e => setDays(Number(e.target.value))} />
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
          { key: "qty", header: "On hand", accessor: r => r.on_hand, width: "80px" },
          { key: "exp", header: "Expires", accessor: r => r.expiration_date, render: r => new Date(r.expiration_date).toLocaleDateString() },
          { key: "left", header: "Days", accessor: r => r.days_until_expiry, width: "80px",
            render: r => r.expired ? <span className="text-danger">expired</span> : <span className={r.days_until_expiry < 30 ? "text-warning" : ""}>{r.days_until_expiry}</span> },
        ]}
      />
    </div>
  );
}
