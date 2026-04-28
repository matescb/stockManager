import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { StorageLocation } from "@/types";

type Stock = { rows: { storage_location_id: string | null; lot_id: string | null; quantity: number }[] };

export default function PartMoveStock() {
  const { partId } = useParams<{ partId: string }>();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data: storage } = useQuery({ queryKey: ["storage"], queryFn: () => api.get<StorageLocation[]>("/storage") });
  const { data: stock } = useQuery({ queryKey: ["part", partId, "stock"], queryFn: () => api.get<Stock>(`/parts/${partId}/stock`) });
  const [src, setSrc] = useState("");
  const [srcLot, setSrcLot] = useState("");
  const [dest, setDest] = useState("");
  const [qty, setQty] = useState<number>(0);
  const [split, setSplit] = useState(false);
  const [comments, setComments] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await api.post("/stock/move", {
        part_id: partId,
        source_storage_location_id: src || null,
        source_lot_id: srcLot || null,
        destination_storage_location_id: dest,
        quantity: Number(qty),
        split_lot: split,
        comments: comments || undefined,
      });
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
      <h3 className="text-md font-semibold">Move stock</h3>
      {err && <div className="text-danger text-sm">{err}</div>}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">From storage</label>
          <select className="input" value={src} onChange={e => setSrc(e.target.value)}>
            <option value="">— any —</option>
            {storage?.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        <div>
          <label className="label">From lot</label>
          <select className="input" value={srcLot} onChange={e => setSrcLot(e.target.value)}>
            <option value="">— any —</option>
            {(stock?.rows ?? []).filter(r => r.lot_id).map(r => (
              <option key={r.lot_id!} value={r.lot_id!}>{r.lot_id} ({r.quantity})</option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label className="label">To storage *</label>
        <select className="input" required value={dest} onChange={e => setDest(e.target.value)}>
          <option value="">— select —</option>
          {storage?.filter(s => !s.archived_at).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>
      <div>
        <label className="label">Quantity *</label>
        <input className="input" type="number" min={1} required value={qty || ""} onChange={e => setQty(Number(e.target.value))} />
      </div>
      <label className="flex items-center gap-2 text-sm cursor-pointer">
        <input type="checkbox" checked={split} onChange={e => setSplit(e.target.checked)} />
        Split lot at destination
      </label>
      <div>
        <label className="label">Comments</label>
        <textarea className="input" rows={2} value={comments} onChange={e => setComments(e.target.value)} />
      </div>
      <button className="btn-primary" disabled={busy}>{busy ? "Moving…" : "Move"}</button>
    </form>
  );
}
