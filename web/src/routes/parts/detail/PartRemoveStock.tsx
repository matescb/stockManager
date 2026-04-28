import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { StorageLocation } from "@/types";

type Stock = { rows: { storage_location_id: string | null; lot_id: string | null; quantity: number }[] };

export default function PartRemoveStock() {
  const { partId } = useParams<{ partId: string }>();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data: storage } = useQuery({ queryKey: ["storage"], queryFn: () => api.get<StorageLocation[]>("/storage") });
  const { data: stock } = useQuery({ queryKey: ["part", partId, "stock"], queryFn: () => api.get<Stock>(`/parts/${partId}/stock`) });
  const [qty, setQty] = useState<number>(0);
  const [location, setLocation] = useState("");
  const [lot, setLot] = useState("");
  const [comments, setComments] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const lots = (stock?.rows ?? []).filter(r => (!location || r.storage_location_id === location));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const payload: any = { part_id: partId, quantity: Number(qty), comments: comments || undefined };
      if (location) payload.storage_location_id = location;
      if (lot) payload.lot_id = lot;
      await api.post("/stock/remove", payload);
      qc.invalidateQueries({ queryKey: ["part", partId] });
      nav(`/parts/${partId}/stock`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card p-4 max-w-2xl space-y-3">
      <h3 className="text-md font-semibold">Remove stock</h3>
      {err && <div className="text-danger text-sm">{err}</div>}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">Source storage</label>
          <select className="input" value={location} onChange={e => setLocation(e.target.value)}>
            <option value="">— any —</option>
            {storage?.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        <div>
          <label className="label">Lot</label>
          <select className="input" value={lot} onChange={e => setLot(e.target.value)}>
            <option value="">— any —</option>
            {lots.filter(l => l.lot_id).map(l => (
              <option key={l.lot_id!} value={l.lot_id!}>{l.lot_id} ({l.quantity})</option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label className="label">Quantity *</label>
        <input className="input" type="number" min={1} required value={qty || ""} onChange={e => setQty(Number(e.target.value))} />
      </div>
      <div>
        <label className="label">Comments</label>
        <textarea className="input" rows={2} value={comments} onChange={e => setComments(e.target.value)} />
      </div>
      <div className="flex gap-2">
        <button className="btn-danger" disabled={busy}>{busy ? "Removing…" : "Remove"}</button>
      </div>
    </form>
  );
}
