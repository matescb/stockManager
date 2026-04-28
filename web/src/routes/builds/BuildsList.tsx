import { Link, NavLink, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { DataTable } from "@/components/DataTable";
import type { Build, Project } from "@/types";

const STATUS_BADGES: Record<Build["status"], string> = {
  planned: "bg-panel2 text-muted",
  in_progress: "bg-warning/20 text-warning",
  complete: "bg-success/20 text-success",
  cancelled: "bg-danger/20 text-danger",
};

export default function BuildsList({ archived = false }: { archived?: boolean }) {
  const nav = useNavigate();
  const { data } = useQuery({
    queryKey: ["builds", { archived }],
    queryFn: () => api.get<Build[]>(`/builds${archived ? "?archived=true" : ""}`),
  });
  const { data: projects } = useQuery({
    queryKey: ["projects"],
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
      <DataTable
        rows={data ?? []}
        rowKey={r => r.id}
        empty="No builds yet."
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
            render: r => r.completed_at ? new Date(r.completed_at).toLocaleString() : <span className="text-muted">—</span>,
          },
        ]}
      />
    </div>
  );
}
