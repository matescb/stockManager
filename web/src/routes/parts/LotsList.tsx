import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import type { Lot, Part } from "@/types";
import { DataTable } from "@/components/DataTable";

export default function LotsList() {
  const { data } = useQuery({ queryKey: ["lots"], queryFn: () => api.get<Lot[]>("/lots") });
  const { data: parts } = useQuery({ queryKey: ["parts"], queryFn: () => api.get<Part[]>("/parts") });
  const partName = new Map(parts?.map(p => [p.id, p.name]) ?? []);
  const nav = useNavigate();

  return (
    <div>
      <h1 className="text-xl font-semibold mb-3">All lots</h1>
      <DataTable
        rows={data ?? []}
        rowKey={r => r.id}
        empty="No lots yet."
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
    </div>
  );
}
