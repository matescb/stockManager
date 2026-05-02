import { useQuery } from "@tanstack/react-query";
import { ScrollText } from "lucide-react";
import { api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { formatDateTime } from "@/lib/format";
import type { Part, StockEntry, StorageLocation } from "@/types";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import PartsTopNav from "@/components/PartsTopNav";
import { Link } from "react-router-dom";

export default function StockHistory() {
  const { data } = useQuery({ queryKey: useWsKey("stock-history"), queryFn: () => api.get<StockEntry[]>("/stock/history?limit=500") });
  const { data: parts } = useQuery({ queryKey: useWsKey("parts"), queryFn: () => api.get<Part[]>("/parts") });
  const { data: storage } = useQuery({ queryKey: useWsKey("storage"), queryFn: () => api.get<StorageLocation[]>("/storage") });
  const partName = new Map(parts?.map(p => [p.id, p.name]) ?? []);
  const sName = new Map(storage?.map(s => [s.id, s.name]) ?? []);
  return (
    <div>
      <PartsTopNav />
      <DataTable
        rows={data ?? []}
        rowKey={r => r.id}
        tableId="stock-history"
        empty={
          <EmptyState
            icon={ScrollText}
            title="No stock activity yet"
            description="Stock movements (add, remove, transfer) will show up here."
          />
        }
        exportFilename="stock-history"
        columns={[
          { key: "occurred_at", header: "Date", accessor: r => r.occurred_at, render: r => formatDateTime(r.occurred_at) },
          { key: "operation_type", header: "Op", accessor: r => r.operation_type },
          { key: "part", header: "Part", accessor: r => partName.get(r.part_id) || r.part_id, render: r => <Link className="text-accent" to={`/parts/${r.part_id}/info`}>{partName.get(r.part_id) || r.part_id}</Link> },
          { key: "qty", header: "Δ", accessor: r => r.quantity_delta },
          { key: "storage", header: "Storage", accessor: r => r.storage_location_id ? (sName.get(r.storage_location_id) || r.storage_location_id) : "" },
          { key: "comments", header: "Comments", accessor: r => r.comments ?? "" },
        ]}
      />
    </div>
  );
}
