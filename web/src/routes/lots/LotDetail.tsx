import { Outlet, useParams, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { wsKey, wsScope } from "@/lib/queryKeys";
import EntityHeader from "@/components/EntityHeader";
import SubNav from "@/components/SubNav";
import type { Lot, Part, StockEntry, StorageLocation } from "@/types";

export function LotLayout() {
  const { lotId } = useParams<{ lotId: string }>();
  const { data } = useQuery({ queryKey: wsKey("lot", lotId), queryFn: () => api.get<Lot>(`/lots/${lotId}`), enabled: !!lotId });
  const { data: parts } = useQuery({ queryKey: wsKey("parts"), queryFn: () => api.get<Part[]>("/parts") });
  if (!data) return <div className="text-muted">Loading…</div>;
  const part = parts?.find(p => p.id === data.part_id);
  const items = [
    { to: `/lots/${data.id}/info`, label: "Info" },
    { to: `/lots/${data.id}/move`, label: "Move" },
    { to: `/lots/${data.id}/adjust`, label: "Adjust" },
    { to: `/lots/${data.id}/history`, label: "History" },
  ];
  return (
    <div>
      <EntityHeader
        title={data.name || data.id}
        subtitle={part ? `Part: ${part.name}` : `Part: ${data.part_id}`}
        idCode={data.id}
      />
      <SubNav items={items} />
      <Outlet context={{ lot: data }} />
    </div>
  );
}

export function LotInfo() {
  const { lotId } = useParams();
  const { data } = useQuery({ queryKey: wsKey("lot", lotId), queryFn: () => api.get<Lot>(`/lots/${lotId}`), enabled: !!lotId });
  if (!data) return null;
  return (
    <div className="card p-4 max-w-2xl space-y-2 text-sm">
      <div><span className="text-muted">Current quantity:</span> <span className="tabular-nums">{data.current_quantity ?? 0}</span></div>
      <div><span className="text-muted">Source:</span> {data.source_type}</div>
      <div><span className="text-muted">Purchase qty:</span> {data.purchase_quantity ?? "—"}</div>
      <div><span className="text-muted">Unit cost:</span> {data.purchase_unit_cost ?? "—"} {data.purchase_currency ?? ""}</div>
      <div><span className="text-muted">Expires:</span> {data.expiration_date ?? "—"}</div>
      <div><span className="text-muted">Parent lot:</span> {data.parent_lot_id ?? "—"}</div>
      <div><span className="text-muted">Comments:</span> {data.comments ?? "—"}</div>
    </div>
  );
}

export function LotMove() {
  const { lotId } = useParams<{ lotId: string }>();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const { data: storage } = useQuery({ queryKey: wsKey("storage"), queryFn: () => api.get<StorageLocation[]>("/storage") });
  const [dest, setDest] = useState("");
  const [qty, setQty] = useState<number>(0);
  const [split, setSplit] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    await api.post(`/lots/${lotId}/move`, {
      part_id: "00000000-0000-0000-0000-000000000000",  // server fills from lot
      destination_storage_location_id: dest,
      quantity: qty,
      split_lot: split,
    });
    qc.invalidateQueries({ queryKey: wsScope(workspaceId) });
    nav(`/lots/${lotId}/info`);
  }
  return (
    <form onSubmit={submit} className="card p-4 max-w-xl space-y-3">
      <div>
        <label className="label">Destination *</label>
        <select className="input" required value={dest} onChange={e => setDest(e.target.value)}>
          <option value="">— select —</option>
          {storage?.filter(s => !s.archived_at).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>
      <div>
        <label className="label">Quantity *</label>
        <input className="input" type="number" min={1} required value={qty || ""} onChange={e => setQty(Number(e.target.value))} />
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={split} onChange={e => setSplit(e.target.checked)} />
        Split lot
      </label>
      <button className="btn-primary">Move</button>
    </form>
  );
}

export function LotAdjust() {
  const { lotId } = useParams();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const [actual, setActual] = useState<number>(0);
  const [comments, setComments] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    await api.post(`/lots/${lotId}/adjust-count`, { actual_quantity: actual, comments });
    qc.invalidateQueries({ queryKey: wsScope(workspaceId) });
  }
  return (
    <form onSubmit={submit} className="card p-4 max-w-xl space-y-3">
      <div>
        <label className="label">Counted quantity</label>
        <input className="input" type="number" required value={actual || ""} onChange={e => setActual(Number(e.target.value))} />
      </div>
      <div>
        <label className="label">Comments</label>
        <textarea className="input" rows={2} value={comments} onChange={e => setComments(e.target.value)} />
      </div>
      <button className="btn-primary">Adjust</button>
    </form>
  );
}

export function LotHistory() {
  const { lotId } = useParams();
  const { data } = useQuery({ queryKey: wsKey("lot", lotId, "history"), queryFn: () => api.get<StockEntry[]>(`/lots/${lotId}/history`) });
  return (
    <div className="card overflow-hidden">
      <table className="table">
        <thead><tr><th>Date</th><th>Op</th><th>Δ</th><th>Storage</th><th>Comments</th></tr></thead>
        <tbody>
          {(data ?? []).map(e => (
            <tr key={e.id}>
              <td>{new Date(e.occurred_at).toLocaleString()}</td>
              <td>{e.operation_type}</td>
              <td className={e.quantity_delta < 0 ? "text-danger" : "text-accent"}>{e.quantity_delta > 0 ? "+" : ""}{e.quantity_delta}</td>
              <td className="font-mono text-xs">{e.storage_location_id ?? "—"}</td>
              <td>{e.comments ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
