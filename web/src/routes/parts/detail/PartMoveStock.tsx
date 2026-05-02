import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { wsKey } from "@/lib/queryKeys";
import type { StorageLocation } from "@/types";

type StockRow = {
  storage_location_id: string | null;
  lot_id: string | null;
  quantity: number;
};
type StockResp = { total_on_hand: number; rows: StockRow[] };
type Lot = { id: string; name: string };

function rowKey(r: StockRow): string {
  return `${r.storage_location_id ?? ""}|${r.lot_id ?? ""}`;
}

export default function PartMoveStock() {
  const { partId } = useParams<{ partId: string }>();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data: storage } = useQuery({
    queryKey: wsKey("storage"),
    queryFn: () => api.get<StorageLocation[]>("/storage"),
  });
  const { data: stock } = useQuery({
    queryKey: wsKey("part", partId, "stock"),
    queryFn: () => api.get<StockResp>(`/parts/${partId}/stock`),
  });
  const { data: lots } = useQuery({
    queryKey: wsKey("part", partId, "lots"),
    queryFn: () => api.get<Lot[]>(`/parts/${partId}/lots`),
  });

  const [sourceKey, setSourceKey] = useState("");
  const [dest, setDest] = useState("");
  const [qty, setQty] = useState<number>(0);
  const [split, setSplit] = useState(false);
  const [comments, setComments] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const sources = useMemo(
    () => (stock?.rows ?? []).filter(r => r.quantity > 0),
    [stock]
  );
  const storageById = useMemo(
    () => new Map((storage ?? []).map(s => [s.id, s])),
    [storage]
  );
  const lotById = useMemo(
    () => new Map((lots ?? []).map(l => [l.id, l])),
    [lots]
  );

  function labelFor(r: StockRow): string {
    const sName = r.storage_location_id
      ? storageById.get(r.storage_location_id)?.name ?? "(unknown bin)"
      : "(no location)";
    const lName = r.lot_id ? lotById.get(r.lot_id)?.name ?? null : null;
    const parts = [sName];
    if (lName) parts.push(`Lot ${lName}`);
    parts.push(`qty=${r.quantity}`);
    return parts.join(" · ");
  }

  const selected = sources.find(r => rowKey(r) === sourceKey) ?? null;
  const maxQty = selected?.quantity ?? 0;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!selected) {
      setErr("Pick a source bag to move from.");
      return;
    }
    if (!dest) {
      setErr("Pick a destination storage.");
      return;
    }
    if (qty <= 0 || qty > maxQty) {
      setErr(`Quantity must be between 1 and ${maxQty}.`);
      return;
    }
    setBusy(true);
    try {
      await api.post("/stock/move", {
        part_id: partId,
        source_storage_location_id: selected.storage_location_id,
        source_lot_id: selected.lot_id,
        destination_storage_location_id: dest,
        quantity: Number(qty),
        split_lot: split,
        comments: comments || undefined,
      });
      qc.invalidateQueries({ queryKey: wsKey("part", partId) });
      qc.invalidateQueries({ queryKey: wsKey("part", partId, "stock") });
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
      <div>
        <label className="label">From *</label>
        {sources.length === 0 ? (
          <div className="text-sm text-muted py-2">
            Nothing on hand for this part.
          </div>
        ) : (
          <select
            className="input"
            value={sourceKey}
            onChange={e => {
              setSourceKey(e.target.value);
              const next = sources.find(r => rowKey(r) === e.target.value);
              if (next && qty > next.quantity) setQty(next.quantity);
            }}
            required
          >
            <option value="" disabled>— pick a bag —</option>
            {sources.map(r => (
              <option key={rowKey(r)} value={rowKey(r)}>{labelFor(r)}</option>
            ))}
          </select>
        )}
      </div>
      <div>
        <label className="label">To storage *</label>
        <select className="input" required value={dest} onChange={e => setDest(e.target.value)}>
          <option value="">— select —</option>
          {storage?.filter(s => !s.archived_at).map(s => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
      </div>
      <div>
        <label className="label">Quantity *</label>
        <input
          className="input"
          type="number"
          min={1}
          max={maxQty || undefined}
          required
          disabled={!selected}
          value={qty || ""}
          onChange={e => setQty(Number(e.target.value))}
        />
        {selected && (
          <div className="text-xs text-muted mt-1">
            Max {maxQty} available in this bag.
          </div>
        )}
      </div>
      <label className="flex items-center gap-2 text-sm cursor-pointer">
        <input type="checkbox" checked={split} onChange={e => setSplit(e.target.checked)} />
        Split lot at destination
      </label>
      <div>
        <label className="label">Comments</label>
        <textarea
          className="input"
          rows={2}
          value={comments}
          onChange={e => setComments(e.target.value)}
        />
      </div>
      <button
        className="btn-primary"
        disabled={busy || !selected || !dest || qty <= 0 || qty > maxQty}
      >
        {busy ? "Moving…" : "Move"}
      </button>
    </form>
  );
}
