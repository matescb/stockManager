import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { StorageLocation } from "@/types";

export default function PartAddStock() {
  const { partId } = useParams<{ partId: string }>();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data: storage } = useQuery({ queryKey: ["storage"], queryFn: () => api.get<StorageLocation[]>("/storage") });
  const [qty, setQty] = useState<number>(0);
  const [location, setLocation] = useState("");
  const [priceMode, setPriceMode] = useState<"none" | "per_component" | "entire_lot">("none");
  const [unitPrice, setUnitPrice] = useState<string>("");
  const [totalPrice, setTotalPrice] = useState<string>("");
  const [currency, setCurrency] = useState("USD");
  const [lotName, setLotName] = useState("");
  const [comments, setComments] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const payload: any = {
        part_id: partId,
        quantity: Number(qty),
        comments: comments || undefined,
      };
      if (location) payload.storage_location_id = location;
      if (priceMode !== "none") {
        payload.price = {
          mode: priceMode,
          currency,
          unit_price: priceMode === "per_component" ? Number(unitPrice) : undefined,
          total_price: priceMode === "entire_lot" ? Number(totalPrice) : undefined,
        };
      }
      if (lotName) payload.lot = { name: lotName };
      await api.post("/stock/add", payload);
      qc.invalidateQueries({ queryKey: ["part", partId] });
      qc.invalidateQueries({ queryKey: ["parts"] });
      nav(`/parts/${partId}/stock`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card p-4 max-w-2xl space-y-3">
      <h3 className="text-md font-semibold">Add stock</h3>
      {err && <div className="text-danger text-sm">{err}</div>}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">Quantity *</label>
          <input className="input" type="number" min={1} required value={qty || ""} onChange={e => setQty(Number(e.target.value))} />
        </div>
        <div>
          <label className="label">Storage location</label>
          <select className="input" value={location} onChange={e => setLocation(e.target.value)}>
            <option value="">— none —</option>
            {storage?.filter(s => !s.archived_at).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className="label">Price mode</label>
          <select className="input" value={priceMode} onChange={e => setPriceMode(e.target.value as any)}>
            <option value="none">No price</option>
            <option value="per_component">Per component</option>
            <option value="entire_lot">Entire lot</option>
          </select>
        </div>
        {priceMode === "per_component" && (
          <div>
            <label className="label">Unit price</label>
            <input className="input" type="number" step="0.0001" value={unitPrice} onChange={e => setUnitPrice(e.target.value)} />
          </div>
        )}
        {priceMode === "entire_lot" && (
          <div>
            <label className="label">Lot total</label>
            <input className="input" type="number" step="0.01" value={totalPrice} onChange={e => setTotalPrice(e.target.value)} />
          </div>
        )}
        {priceMode !== "none" && (
          <div>
            <label className="label">Currency</label>
            <input className="input" maxLength={3} value={currency} onChange={e => setCurrency(e.target.value.toUpperCase())} />
          </div>
        )}
      </div>
      <div>
        <label className="label">Lot name (optional)</label>
        <input className="input" value={lotName} onChange={e => setLotName(e.target.value)} placeholder="LOT-2026-001" />
      </div>
      <div>
        <label className="label">Comments</label>
        <textarea className="input" rows={2} value={comments} onChange={e => setComments(e.target.value)} />
      </div>
      <div className="flex gap-2">
        <button className="btn-primary" disabled={busy}>{busy ? "Adding…" : "Add"}</button>
      </div>
    </form>
  );
}
