import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Package } from "lucide-react";
import { api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import type { Lot, Part } from "@/types";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import PartsTopNav from "@/components/PartsTopNav";
import QueryStateBoundary from "@/components/QueryStateBoundary";

export default function LotsList() {
  const query = useQuery({ queryKey: useWsKey("lots"), queryFn: () => api.get<Lot[]>("/lots") });
  const { data } = query;
  const { data: parts } = useQuery({ queryKey: useWsKey("parts"), queryFn: () => api.get<Part[]>("/parts") });
  const partName = new Map(parts?.map(p => [p.id, p.name]) ?? []);
  const nav = useNavigate();

  return (
    <div>
      <PartsTopNav />
      <QueryStateBoundary query={query} resourceLabel="lots">
      <DataTable
        rows={data ?? []}
        rowKey={r => r.id}
        tableId="lots"
        empty={
          <EmptyState
            icon={Package}
            title="No lots yet"
            description="Lots are created when you add stock with a price or a name."
          />
        }
        exportFilename="lots"
        onRowClick={r => nav(`/lots/${r.id}/info`)}
        columns={[
          { key: "name", header: "Lot", accessor: r => r.name ?? r.id },
          { key: "part", header: "Part", accessor: r => partName.get(r.part_id) || r.part_id },
          { key: "quantity", header: "On hand", accessor: r => r.current_quantity ?? 0 },
          { key: "unit_cost", header: "Unit cost", accessor: r => r.purchase_unit_cost ?? "" },
          { key: "currency", header: "Cur", accessor: r => r.purchase_currency ?? "" },
          { key: "created_at", header: "Created", accessor: r => r.created_at, render: r => new Date(r.created_at).toLocaleDateString() },
        ]}
      />
      </QueryStateBoundary>
    </div>
  );
}
