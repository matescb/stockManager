import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Lot } from "@/types";
import { DataTable } from "@/components/DataTable";

export default function PartLots() {
  const { partId } = useParams();
  const nav = useNavigate();
  const { data } = useQuery({ queryKey: ["part", partId, "lots"], queryFn: () => api.get<Lot[]>(`/parts/${partId}/lots`) });
  return (
    <DataTable
      rows={data ?? []}
      rowKey={r => r.id}
      tableId="part-lots"
      empty="No lots."
      onRowClick={r => nav(`/lots/${r.id}/info`)}
      exportFilename="lots"
      columns={[
        { key: "name", header: "Name", accessor: r => r.name ?? r.id },
        { key: "purchase_quantity", header: "Purchased", accessor: r => r.purchase_quantity ?? "" },
        { key: "purchase_unit_cost", header: "Unit cost", accessor: r => r.purchase_unit_cost ?? "" },
        { key: "purchase_currency", header: "Currency", accessor: r => r.purchase_currency ?? "" },
        { key: "expiration_date", header: "Expires", accessor: r => r.expiration_date ?? "" },
        { key: "source_type", header: "Source", accessor: r => r.source_type },
        { key: "created_at", header: "Created", accessor: r => r.created_at, render: r => new Date(r.created_at).toLocaleString() },
      ]}
    />
  );
}
