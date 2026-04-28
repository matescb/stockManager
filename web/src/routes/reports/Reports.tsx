import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { DataTable } from "@/components/DataTable";
import type { Project } from "@/types";

type LowStockRow = {
  part_id: string;
  name: string;
  manufacturer: string | null;
  mpn: string | null;
  on_hand: number;
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

export default function ReportsLayout() {
  return (
    <div>
      <div className="flex items-center gap-1 mb-3">
        <NavLink end to="/reports" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Low stock</NavLink>
        <NavLink to="/reports/value" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Stock value</NavLink>
        <NavLink to="/reports/bom" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>BOM shortage</NavLink>
        <NavLink to="/reports/expiring" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Expiring lots</NavLink>
      </div>
      <Outlet />
    </div>
  );
}

export function LowStockReport() {
  const { data, isLoading } = useQuery({
    queryKey: ["report", "low-stock"],
    queryFn: () => api.get<LowStockRow[]>("/reports/low-stock"),
  });
  if (isLoading) return <div className="text-muted">Loading…</div>;
  return (
    <DataTable
      rows={data ?? []}
      rowKey={r => r.part_id}
      empty="No parts below their threshold."
      exportFilename="low-stock"
      columns={[
        { key: "name", header: "Part", accessor: r => r.name, render: r => <Link className="text-accent" to={`/parts/${r.part_id}/info`}>{r.name}</Link> },
        { key: "mpn", header: "MPN", accessor: r => r.mpn ?? "" },
        { key: "manufacturer", header: "Manufacturer", accessor: r => r.manufacturer ?? "" },
        { key: "on_hand", header: "On hand", accessor: r => r.on_hand, width: "80px" },
        { key: "threshold", header: "Threshold", accessor: r => r.threshold, width: "100px" },
        { key: "short_by", header: "Short by", accessor: r => r.short_by, width: "100px",
          render: r => <span className="tabular-nums text-danger">{r.short_by}</span> },
      ]}
    />
  );
}

export function StockValueReport() {
  const { data, isLoading } = useQuery({
    queryKey: ["report", "stock-value"],
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
        empty="No purchase-cost-tagged stock yet. Add stock with a price to populate this report."
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
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects") });
  const [projectId, setProjectId] = useState("");
  const [qty, setQty] = useState(1);
  const { data } = useQuery({
    queryKey: ["report", "bom", projectId, qty],
    queryFn: () => api.get<Shortage>(`/reports/bom-shortage?project_id=${projectId}&quantity=${qty}`),
    enabled: !!projectId && qty > 0,
  });

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
        <DataTable
          rows={data.rows}
          rowKey={r => r.part_id}
          empty="No consumable BOM lines."
          exportFilename="bom-shortage"
          columns={[
            { key: "name", header: "Part", accessor: r => r.part_name, render: r => <Link className="text-accent" to={`/parts/${r.part_id}/info`}>{r.part_name}</Link> },
            { key: "required", header: "Required", accessor: r => r.required, width: "100px" },
            { key: "available", header: "On hand", accessor: r => r.available, width: "100px" },
            { key: "short_by", header: "Short by", accessor: r => r.short_by, width: "100px",
              render: r => r.short_by ? <span className="tabular-nums text-danger">{r.short_by}</span> : <span className="text-muted">—</span> },
          ]}
        />
      )}
    </div>
  );
}

export function ExpiringLotsReport() {
  const [days, setDays] = useState(90);
  const { data } = useQuery({
    queryKey: ["report", "expiring", days],
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
        empty="No lots expiring in this window."
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
