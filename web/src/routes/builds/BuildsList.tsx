import { Link, NavLink, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Hammer } from "lucide-react";
import { api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { formatDateTime } from "@/lib/format";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import QueryStateBoundary from "@/components/QueryStateBoundary";
import type { Build, Project } from "@/types";

const STATUS_BADGES: Record<Build["status"], string> = {
  planned: "bg-panel2 text-muted",
  in_progress: "bg-warning/20 text-warning",
  complete: "bg-success/20 text-success",
  cancelled: "bg-danger/20 text-danger",
};

export default function BuildsList({ archived = false }: { archived?: boolean }) {
  const nav = useNavigate();
  const query = useQuery({
    queryKey: useWsKey("builds", { archived }),
    queryFn: () => api.get<Build[]>(`/builds${archived ? "?archived=true" : ""}`),
  });
  const { data } = query;
  const { data: projects } = useQuery({
    queryKey: useWsKey("projects"),
    queryFn: () => api.get<Project[]>("/projects"),
  });
  const projectsById = new Map(projects?.map(p => [p.id, p]) ?? []);

  return (
    <div>
      <div className="flex items-center gap-1 mb-3">
        <NavLink to="/builds" end className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Builds</NavLink>
        <NavLink to="/builds/archived" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Archived</NavLink>
        <Link to="/builds/create" className="btn-primary ml-auto">+ Build</Link>
      </div>
      <QueryStateBoundary query={query} resourceLabel="builds">
      <DataTable
        rows={data ?? []}
        rowKey={r => r.id}
        tableId="builds"
        empty={
          archived ? (
            <EmptyState
              icon={Hammer}
              title="No archived builds"
              description="Archived builds will appear here."
            />
          ) : (
            <EmptyState
              icon={Hammer}
              title="No builds yet"
              description="Plan a build against a project to reserve and consume parts."
              action={{ label: "+ Build", to: "/builds/create" }}
            />
          )
        }
        exportFilename="builds"
        onRowClick={r => nav(`/builds/${r.id}`)}
        columns={[
          { key: "name", header: "Name", accessor: r => r.name },
          {
            key: "project",
            header: "Project",
            accessor: r => projectsById.get(r.project_id)?.name ?? r.project_id,
          },
          { key: "qty", header: "Qty", accessor: r => r.quantity, width: "60px" },
          {
            key: "status",
            header: "Status",
            accessor: r => r.status,
            render: r => <span className={`pill ${STATUS_BADGES[r.status] ?? ""}`}>{r.status}</span>,
          },
          {
            key: "completed",
            header: "Completed",
            accessor: r => r.completed_at ?? "",
            render: r => r.completed_at ? formatDateTime(r.completed_at) : <span className="text-muted">—</span>,
          },
        ]}
      />
      </QueryStateBoundary>
    </div>
  );
}
