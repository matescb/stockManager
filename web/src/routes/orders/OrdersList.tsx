import { Link, NavLink, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ShoppingCart } from "lucide-react";
import { api } from "@/lib/api";
import { wsKey } from "@/lib/queryKeys";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import QueryStateBoundary from "@/components/QueryStateBoundary";
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
  const query = useQuery({
    queryKey: wsKey("orders", { archived }),
    queryFn: () => api.get<Order[]>(`/orders${archived ? "?archived=true" : ""}`),
  });
  const { data } = query;

  return (
    <div>
      <div className="flex items-center gap-1 mb-3">
        <NavLink to="/orders" end className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Orders</NavLink>
        <NavLink to="/orders/archived" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Archived</NavLink>
        <Link to="/orders/create" className="btn-primary ml-auto">+ Order</Link>
      </div>
      <QueryStateBoundary query={query} resourceLabel="orders">
      <DataTable
        rows={data ?? []}
        rowKey={r => r.id}
        tableId="orders"
        empty={
          archived ? (
            <EmptyState
              icon={ShoppingCart}
              title="No archived orders"
              description="Archived orders will appear here."
            />
          ) : (
            <EmptyState
              icon={ShoppingCart}
              title="No orders yet"
              description="Create a purchase order to track expected stock."
              action={{ label: "+ Order", to: "/orders/create" }}
            />
          )
        }
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
      </QueryStateBoundary>
    </div>
  );
}
