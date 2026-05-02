import { Link, NavLink, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Warehouse } from "lucide-react";
import { api } from "@/lib/api";
import { wsKey } from "@/lib/queryKeys";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import type { StorageLocation } from "@/types";

export default function StorageList({ archived = false }: { archived?: boolean }) {
  const nav = useNavigate();
  const { data } = useQuery({
    queryKey: wsKey("storage", { archived }),
    queryFn: () => api.get<StorageLocation[]>(`/storage${archived ? "?archived=true" : ""}`),
  });
  return (
    <div>
      <div className="flex items-center gap-1 mb-3">
        <NavLink to="/storage" end className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Storage</NavLink>
        <NavLink to="/storage/archived" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Archived</NavLink>
        <Link to="/storage/create" className="btn-primary ml-auto">+ Storage</Link>
      </div>
      <DataTable
        rows={data ?? []}
        rowKey={r => r.id}
        tableId="storage"
        empty={
          archived ? (
            <EmptyState
              icon={Warehouse}
              title="No archived storage"
              description="Archived storage locations will appear here."
            />
          ) : (
            <EmptyState
              icon={Warehouse}
              title="No storage locations yet"
              description="Add a shelf, bin, or reel to organise your inventory."
              action={{ label: "+ Storage", to: "/storage/create" }}
            />
          )
        }
        exportFilename="storage"
        onRowClick={r => nav(`/storage/${r.id}/info`)}
        columns={[
          { key: "name", header: "Location", accessor: r => r.name },
          { key: "description", header: "Description", accessor: r => r.description ?? "" },
          { key: "single_part_only", header: "Single-part", accessor: r => r.single_part_only ? "yes" : "" },
          { key: "is_full", header: "Full", accessor: r => r.is_full ? "yes" : "" },
        ]}
      />
    </div>
  );
}
