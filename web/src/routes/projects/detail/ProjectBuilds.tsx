import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Hammer } from "lucide-react";
import { api } from "@/lib/api";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import type { Build } from "@/types";

export default function ProjectBuilds() {
  const { projectId } = useParams<{ projectId: string }>();
  const { data } = useQuery({
    queryKey: ["builds", { project: projectId }],
    queryFn: () => api.get<Build[]>(`/builds?project_id=${projectId}`),
    enabled: !!projectId,
  });

  return (
    <div>
      <div className="flex items-center mb-3">
        <h3 className="text-md font-semibold">Builds against this project</h3>
        <Link to={`/builds/create?project_id=${projectId}`} className="btn-primary ml-auto">+ Build</Link>
      </div>
      <DataTable
        rows={data ?? []}
        rowKey={r => r.id}
        tableId="project-builds"
        empty={
          <EmptyState
            icon={Hammer}
            title="No builds against this project yet"
            description="Plan a build to reserve stock and track consumption."
            action={{ label: "+ Build", to: `/builds/create?project_id=${projectId}` }}
          />
        }
        columns={[
          { key: "name", header: "Name", accessor: r => r.name, render: r => <Link className="text-accent" to={`/builds/${r.id}`}>{r.name}</Link> },
          { key: "qty", header: "Qty", accessor: r => r.quantity, width: "60px" },
          { key: "status", header: "Status", accessor: r => r.status },
          {
            key: "completed",
            header: "Completed",
            accessor: r => r.completed_at ?? "",
            render: r => r.completed_at ? new Date(r.completed_at).toLocaleString() : <span className="text-muted">—</span>,
          },
        ]}
      />
    </div>
  );
}
