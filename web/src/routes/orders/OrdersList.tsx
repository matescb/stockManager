import { Link, NavLink, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { DataTable } from "@/components/DataTable";
import type { Order } from "@/types";

const STATUS_BADGES: Record<Order["status"], string> = {
  draft: "bg-panel2 text-muted",
  open: "bg-accent/20 text-accent",
  partial: "bg-warning/20 text-warning",
  received: "bg-success/20 text-success",
  cancelled: "bg-danger/20 text-danger",
};

export default function OrdersList({ archived = false }: { archived?: boolean }) {
  const nav = useNavigate();
  const { data } = useQuery({
    queryKey: ["orders", { archived }],
    queryFn: () => api.get<Order[]>(`/orders${archived ? "?archived=true" : ""}`),
  });

  return (
    <div>
      <div className="flex items-center gap-1 mb-3">
        <NavLink to="/orders" end className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Orders</NavLink>
        <NavLink to="/orders/archived" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Archived</NavLink>
        <Link to="/orders/create" className="btn-primary ml-auto">+ Order</Link>
      </div>
      <DataTable
        rows={data ?? []}
        rowKey={r => r.id}
        empty="No orders yet."
        exportFilename="orders"
        onRowClick={r => nav(`/orders/${r.id}`)}
        columns={[
          { key: "name", header: "Name", accessor: r => r.name },
          { key: "supplier", header: "Supplier", accessor: r => r.supplier ?? "" },
          {
            key: "status",
            header: "Status",
            accessor: r => r.status,
            render: r => (
              <span className={`pill ${STATUS_BADGES[r.status] ?? ""}`}>{r.status}</span>
            ),
          },
          {
            key: "progress",
            header: "Received / Ordered",
            accessor: r => `${r.totals.received}/${r.totals.ordered}`,
            render: r => (
              <span className="tabular-nums text-sm">
                {r.totals.received} / {r.totals.ordered}
              </span>
            ),
          },
          {
            key: "expected_on",
            header: "Expected",
            accessor: r => r.expected_on ?? "",
            render: r => r.expected_on ? new Date(r.expected_on).toLocaleDateString() : <span className="text-muted">—</span>,
          },
        ]}
      />
    </div>
  );
}
