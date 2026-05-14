import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { formatDateTime } from "@/lib/format";
import type { StockEntry, StorageLocation } from "@/types";
import { DataTable } from "@/components/DataTable";

export default function PartHistory() {
  const { partId } = useParams();
  const { data, isError, error } = useQuery({
    queryKey: useWsKey("part", partId, "history"),
    queryFn: async ({ signal }) => {
      // history endpoint is global; filter client-side by part for now
      const rows = await api.get<StockEntry[]>("/stock/history?limit=1000", { signal });
      return rows.filter(r => r.part_id === partId);
    },
  });
  const { data: storage } = useQuery({ queryKey: useWsKey("storage"), queryFn: ({ signal }) => api.get<StorageLocation[]>("/storage", { signal }) });
  const sName = new Map(storage?.map(s => [s.id, s.name]) ?? []);
  if (isError) return <div className="text-red-600 text-sm p-4">Failed to load history. {error instanceof ApiError ? error.userMessage : ""}</div>;
  return (
    <DataTable
      rows={data ?? []}
      rowKey={r => r.id}
      tableId="part-history"
      empty="No stock history."
      exportFilename="stock-history"
      columns={[
        { key: "occurred_at", header: "Date", accessor: r => r.occurred_at, render: r => formatDateTime(r.occurred_at) },
        { key: "operation_type", header: "Op", accessor: r => r.operation_type },
        { key: "quantity_delta", header: "Δ", accessor: r => r.quantity_delta, render: r => <span className={r.quantity_delta < 0 ? "text-danger" : "text-accent"}>{r.quantity_delta > 0 ? "+" : ""}{r.quantity_delta}</span> },
        { key: "storage", header: "Storage", accessor: r => r.storage_location_id ? (sName.get(r.storage_location_id) || r.storage_location_id) : "" },
        { key: "lot_id", header: "Lot", accessor: r => r.lot_id ?? "" },
        { key: "comments", header: "Comments", accessor: r => r.comments ?? "" },
      ]}
    />
  );
}
