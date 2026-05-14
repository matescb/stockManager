import { Outlet, useParams, useNavigate, useOutletContext } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useWsKey, lotMutationKeys } from "@/lib/queryKeys";
import { formatDateTime } from "@/lib/format";
import EntityHeader from "@/components/EntityHeader";
import SubNav from "@/components/SubNav";
import type { Lot, Part, StockEntry, StorageLocation } from "@/types";

export function LotLayout() {
  const { lotId } = useParams<{ lotId: string }>();
  const { data, isError, error } = useQuery({ queryKey: useWsKey("lot", lotId), queryFn: ({ signal }) => api.get<Lot>(`/lots/${lotId}`, { signal }), enabled: !!lotId });
  const { data: parts } = useQuery({ queryKey: useWsKey("parts"), queryFn: ({ signal }) => api.get<Part[]>("/parts?limit=200", { signal }) });
  if (isError) return <div className="text-red-600 text-sm p-4">Failed to load lot. {error instanceof ApiError ? error.userMessage : ""}</div>;
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
      <Outlet key={data.id} context={{ lot: data }} />
    </div>
  );
}

export function LotInfo() {
  const { lotId } = useParams();
  const { data } = useQuery({ queryKey: useWsKey("lot", lotId), queryFn: ({ signal }) => api.get<Lot>(`/lots/${lotId}`, { signal }), enabled: !!lotId });
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

type PartStockResp = {
  total_on_hand: number;
  rows: { storage_location_id: string | null; lot_id: string | null; quantity: number }[];
};

export function LotMove() {
  const { lotId } = useParams<{ lotId: string }>();
  const { lot } = useOutletContext<{ lot: Lot }>();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const { data: storage } = useQuery({ queryKey: useWsKey("storage"), queryFn: ({ signal }) => api.get<StorageLocation[]>("/storage", { signal }) });
  // The Lot resource itself doesn't carry a single storage_location_id
  // (a lot can be split across bins via prior moves), so we read the
  // part's stock breakdown to discover which bins currently hold this
  // lot — those are the source bins the move will drain. Without this,
  // the source bin's StorageInfo / StorageHistory go stale on success.
  const { data: partStock } = useQuery({
    queryKey: useWsKey("part", lot.part_id, "stock"),
    queryFn: ({ signal }) => api.get<PartStockResp>(`/parts/${lot.part_id}/stock`, { signal }),
  });
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
    const sourceIds = (partStock?.rows ?? [])
      .filter(r => r.lot_id === lot.id && r.storage_location_id)
      .map(r => r.storage_location_id as string);
    const storageIds = Array.from(new Set([...sourceIds, dest].filter(Boolean) as string[]));
    for (const k of lotMutationKeys(workspaceId, lot, storageIds))
      qc.invalidateQueries({ queryKey: k });
    nav(`/lots/${lotId}/info`);
  }
  return (
    <form onSubmit={submit} className="card p-4 max-w-xl space-y-3">
      <div>
        <label className="label" htmlFor="lot-move-dest">Destination *</label>
        <select id="lot-move-dest" className="input" required value={dest} onChange={e => setDest(e.target.value)}>
          <option value="">— select —</option>
          {storage?.filter(s => !s.archived_at).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>
      <div>
        <label className="label" htmlFor="lot-move-qty">Quantity *</label>
        <input id="lot-move-qty" className="input" type="number" min={1} required value={qty || ""} onChange={e => setQty(Number(e.target.value))} />
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
  const { lot } = useOutletContext<{ lot: Lot }>();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const [actual, setActual] = useState<number>(0);
  const [comments, setComments] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    await api.post(`/lots/${lotId}/adjust-count`, { actual_quantity: actual, comments });
    for (const k of lotMutationKeys(workspaceId, lot))
      qc.invalidateQueries({ queryKey: k });
  }
  return (
    <form onSubmit={submit} className="card p-4 max-w-xl space-y-3">
      <div>
        <label className="label" htmlFor="lot-adjust-qty">Counted quantity</label>
        <input id="lot-adjust-qty" className="input" type="number" required value={actual || ""} onChange={e => setActual(Number(e.target.value))} />
      </div>
      <div>
        <label className="label" htmlFor="lot-adjust-comments">Comments</label>
        <textarea id="lot-adjust-comments" className="input" rows={2} value={comments} onChange={e => setComments(e.target.value)} />
      </div>
      <button className="btn-primary">Adjust</button>
    </form>
  );
}

export function LotHistory() {
  const { lotId } = useParams();
  const { data, isError, error } = useQuery({ queryKey: useWsKey("lot", lotId, "history"), queryFn: ({ signal }) => api.get<StockEntry[]>(`/lots/${lotId}/history?limit=200`, { signal }) });
  if (isError) return <div className="text-red-600 text-sm p-4">Failed to load history. {error instanceof ApiError ? error.userMessage : ""}</div>;
  return (
    <div className="card overflow-hidden">
      <table className="table">
        <thead><tr><th>Date</th><th>Op</th><th>Δ</th><th>Storage</th><th>Comments</th></tr></thead>
        <tbody>
          {(data ?? []).map(e => (
            <tr key={e.id}>
              <td>{formatDateTime(e.occurred_at)}</td>
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
