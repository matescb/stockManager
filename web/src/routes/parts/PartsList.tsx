import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Boxes } from "lucide-react";
import { api } from "@/lib/api";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import PartsTopNav from "@/components/PartsTopNav";
import type { Part } from "@/types";

export default function PartsList({ archived = false }: { archived?: boolean }) {
  const nav = useNavigate();
  const { data, isLoading } = useQuery({
    queryKey: ["parts", { archived }],
    queryFn: () => api.get<Part[]>(`/parts${archived ? "?archived=true" : ""}`),
  });

  return (
    <div>
      <PartsTopNav
        rightAccessory={
          <>
            <Link to="/parts/scan" className="btn">Scan</Link>
            <Link to="/parts/scan-import" className="btn">Scan to import</Link>
            <Link to="/parts/create" className="btn-primary">+ Part</Link>
          </>
        }
      />
      {isLoading ? (
        <div className="text-muted">Loading…</div>
      ) : (
        <DataTable
          rows={data ?? []}
          rowKey={(r) => r.id}
          tableId="parts"
          searchPlaceholder="Search parts…"
          empty={
            archived ? (
              <EmptyState
                icon={Boxes}
                title="No archived parts"
                description="Archived parts will appear here."
              />
            ) : (
              <EmptyState
                icon={Boxes}
                title="No parts yet"
                description="Create your first part to start tracking stock."
                action={{ label: "+ Part", to: "/parts/create" }}
              />
            )
          }
          exportFilename="parts"
          onRowClick={(r) => nav(`/parts/${r.id}/info`)}
          columns={[
            { key: "part_type", header: "Type", accessor: r => r.part_type, width: "100px" },
            { key: "name", header: "Part", accessor: r => r.name, render: r => <span className="font-medium">{r.name}</span> },
            { key: "mpn", header: "MPN", accessor: r => r.mpn ?? "" },
            { key: "manufacturer", header: "Manufacturer", accessor: r => r.manufacturer ?? "" },
            { key: "footprint", header: "Footprint", accessor: r => r.footprint ?? "" },
            { key: "on_hand", header: "Stock", accessor: r => r.on_hand ?? 0, width: "80px" },
            { key: "reserved", header: "Reserved", accessor: r => r.reserved ?? 0, width: "100px", hidden: true },
          ]}
        />
      )}
    </div>
  );
}
