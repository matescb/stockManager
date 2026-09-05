import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { formatQuantity } from "@/lib/format";
import type { StorageLocation } from "@/types";

type StockResp = {
  total_on_hand: number;
  rows: { storage_location_id: string | null; lot_id: string | null; quantity: number }[];
};

export default function PartStock() {
  const { partId } = useParams();
  const { data, isError, error } = useQuery({
    queryKey: useWsKey("part", partId, "stock"),
    queryFn: ({ signal }) => api.get<StockResp>(`/parts/${partId}/stock`, { signal }),
  });
  const { data: storage } = useQuery({ queryKey: useWsKey("storage"), queryFn: ({ signal }) => api.get<StorageLocation[]>("/storage", { signal }) });
  const storageById = new Map(storage?.map(s => [s.id, s.name]) ?? []);

  if (isError) return <div className="text-red-600 text-sm p-4">Failed to load stock. {error instanceof ApiError ? error.userMessage : ""}</div>;
  if (!data) return <div className="text-muted">Loading…</div>;
  return (
    <div>
      <div className="card p-4 mb-4">
        <div className="text-sm text-muted">Total on hand</div>
        <div data-testid="part-stock-on-hand" className="text-2xl font-semibold tabular-nums">{formatQuantity(data.total_on_hand)}</div>
      </div>
      <div className="card overflow-hidden">
        <table className="table">
          <thead>
            <tr>
              <th>Storage</th>
              <th>Lot</th>
              <th>Quantity</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.length === 0 && (
              <tr><td colSpan={3} className="text-center py-6 text-muted">No on-hand stock.</td></tr>
            )}
            {data.rows.map((r, i) => (
              <tr key={i}>
                <td>{r.storage_location_id ? (storageById.get(r.storage_location_id) || r.storage_location_id) : <span className="text-muted">—</span>}</td>
                <td className="font-mono text-xs">{r.lot_id || <span className="text-muted">—</span>}</td>
                <td className="tabular-nums">{formatQuantity(r.quantity)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
