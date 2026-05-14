import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import { InlineQueryError } from "@/components/QueryStateBoundary";
import type { StorageLocation } from "@/types";

/**
 * Mirror of `backend/app/domain/stock/schemas.py::RemoveStockIn`
 * (extra="forbid").
 */
type RemoveStockRequest = {
  part_id: string;
  quantity: number;
  storage_location_id?: string;
  lot_id?: string;
  comments?: string;
};

type StockRow = {
  storage_location_id: string | null;
  lot_id: string | null;
  quantity: number;
};
type StockResp = { total_on_hand: number; rows: StockRow[] };
type Lot = { id: string; name: string };

/** Stable key for a (storage, lot) combo. The combined Source picker
 *  selects by this key; on submit we map it back to the row. */
function rowKey(r: StockRow): string {
  return `${r.storage_location_id ?? ""}|${r.lot_id ?? ""}`;
}

export default function PartRemoveStock() {
  const { partId } = useParams<{ partId: string }>();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const storageQuery = useQuery({
    queryKey: useWsKey("storage"),
    queryFn: ({ signal }) => api.get<StorageLocation[]>("/storage", { signal }),
  });
  const stockQuery = useQuery({
    queryKey: useWsKey("part", partId, "stock"),
    queryFn: ({ signal }) => api.get<StockResp>(`/parts/${partId}/stock`, { signal }),
  });
  const lotsQuery = useQuery({
    queryKey: useWsKey("part", partId, "lots"),
    queryFn: ({ signal }) => api.get<Lot[]>(`/parts/${partId}/lots`, { signal }),
  });
  const { data: storage } = storageQuery;
  const { data: stock } = stockQuery;
  const { data: lots } = lotsQuery;

  const [sourceKey, setSourceKey] = useState("");
  const [qty, setQty] = useState<number>(0);
  const [comments, setComments] = useState("");
  const [err, setErr] = useState<string | null>(null);

  // Only rows with positive quantity are valid sources — you can't
  // remove from somewhere that has nothing.
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

  // FE2-006: ledger remove also appends an entry — same double-submit
  // hazard as add. Single mutationKey per part is enough since the
  // form only has one in-flight remove at a time.
  const removeMutation = useApiMutation<unknown, RemoveStockRequest>({
    mutationKey: ["part", partId, "stock-remove"],
    mutationFn: (payload) => api.post<unknown, RemoveStockRequest>("/stock/remove", payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", partId) });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", partId, "stock") });
      nav(`/parts/${partId}/stock`);
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.userMessage : "Failed");
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!selected) {
      setErr("Pick a source bag to remove from.");
      return;
    }
    if (qty <= 0 || qty > maxQty) {
      setErr(`Quantity must be between 1 and ${maxQty}.`);
      return;
    }
    const payload: RemoveStockRequest = {
      part_id: partId!,
      quantity: Number(qty),
      comments: comments || undefined,
    };
    if (selected.storage_location_id) payload.storage_location_id = selected.storage_location_id;
    if (selected.lot_id) payload.lot_id = selected.lot_id;
    removeMutation.mutate(payload);
  }

  const busy = removeMutation.isPending;

  return (
    <form onSubmit={submit} className="card p-4 max-w-2xl space-y-3">
      <h3 className="text-md font-semibold">Remove stock</h3>
      {err && <div className="text-danger text-sm">{err}</div>}
      <InlineQueryError query={stockQuery} label="current stock" />
      <InlineQueryError query={storageQuery} label="storage locations" />
      <InlineQueryError query={lotsQuery} label="lots" />
      <div>
        <label className="label" htmlFor="remove-stock-source">Source *</label>
        {sources.length === 0 ? (
          <div className="text-sm text-muted py-2">
            Nothing on hand for this part.
          </div>
        ) : (
          <select
            id="remove-stock-source"
            className="input"
            value={sourceKey}
            onChange={e => {
              setSourceKey(e.target.value);
              // Cap qty at the new source's max so you don't carry an
              // over-limit value across selections.
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
        <label className="label" htmlFor="remove-stock-qty">Quantity *</label>
        <input
          id="remove-stock-qty"
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
      <div>
        <label className="label" htmlFor="remove-stock-comments">Comments</label>
        <textarea
          id="remove-stock-comments"
          className="input"
          rows={2}
          value={comments}
          onChange={e => setComments(e.target.value)}
        />
      </div>
      <div className="flex gap-2">
        <button
          className="btn-danger"
          disabled={busy || !selected || qty <= 0 || qty > maxQty}
        >
          {busy ? "Removing…" : "Remove"}
        </button>
      </div>
    </form>
  );
}
