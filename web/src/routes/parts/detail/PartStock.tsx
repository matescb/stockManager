import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { StorageLocation } from "@/types";

type StockResp = {
  total_on_hand: number;
  rows: { storage_location_id: string | null; lot_id: string | null; quantity: number }[];
};

export default function PartStock() {
  const { partId } = useParams();
  const { data } = useQuery({
    queryKey: ["part", partId, "stock"],
    queryFn: () => api.get<StockResp>(`/parts/${partId}/stock`),
  });
  const { data: storage } = useQuery({ queryKey: ["storage"], queryFn: () => api.get<StorageLocation[]>("/storage") });
  const storageById = new Map(storage?.map(s => [s.id, s.name]) ?? []);

  if (!data) return <div className="text-muted">Loading…</div>;
  return (
    <div>
      <div className="card p-4 mb-4">
        <div className="text-sm text-muted">Total on hand</div>
        <div className="text-2xl font-semibold tabular-nums">{data.total_on_hand}</div>
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
                <td className="tabular-nums">{r.quantity}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
