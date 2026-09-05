import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { formatDateTime } from "@/lib/format";
import type { Lot } from "@/types";
import { DataTable, quantityColumn } from "@/components/DataTable";
import QueryStateBoundary from "@/components/QueryStateBoundary";

export default function PartLots() {
  const { partId } = useParams();
  const nav = useNavigate();
  const lotsQuery = useQuery({ queryKey: useWsKey("part", partId, "lots"), queryFn: ({ signal }) => api.get<Lot[]>(`/parts/${partId}/lots`, { signal }) });
  const { data } = lotsQuery;
  return (
    <QueryStateBoundary query={lotsQuery} resourceLabel="lots">
    <DataTable
      rows={data ?? []}
      rowKey={r => r.id}
      tableId="part-lots"
      empty="No lots."
      onRowClick={r => nav(`/lots/${r.id}/info`)}
      exportFilename="lots"
      columns={[
        { key: "name", header: "Name", accessor: r => r.name ?? r.id },
        quantityColumn<Lot>({ key: "purchase_quantity", header: "Purchased", value: r => r.purchase_quantity }),
        { key: "purchase_unit_cost", header: "Unit cost", accessor: r => r.purchase_unit_cost ?? "" },
        { key: "purchase_currency", header: "Currency", accessor: r => r.purchase_currency ?? "" },
        { key: "expiration_date", header: "Expires", accessor: r => r.expiration_date ?? "" },
        { key: "source_type", header: "Source", accessor: r => r.source_type },
        { key: "created_at", header: "Created", accessor: r => r.created_at, render: r => formatDateTime(r.created_at) },
      ]}
    />
    </QueryStateBoundary>
  );
}
