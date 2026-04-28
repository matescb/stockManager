import { Link, NavLink, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { DataTable } from "@/components/DataTable";
import type { Project } from "@/types";

export default function ProjectsList({ archived = false }: { archived?: boolean }) {
  const nav = useNavigate();
  const { data } = useQuery({
    queryKey: ["projects", { archived }],
    queryFn: () => api.get<Project[]>(`/projects${archived ? "?archived=true" : ""}`),
  });
  return (
    <div>
      <div className="flex items-center gap-1 mb-3">
        <NavLink to="/projects" end className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Projects</NavLink>
        <NavLink to="/projects/archived" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Archived</NavLink>
        <Link to="/projects/create" className="btn-primary ml-auto">+ Project</Link>
      </div>
      <DataTable
        rows={data ?? []}
        rowKey={r => r.id}
        empty="No projects yet."
        exportFilename="projects"
        onRowClick={r => nav(`/projects/${r.id}/data`)}
        columns={[
          { key: "name", header: "Name", accessor: r => r.name },
          { key: "description", header: "Description", accessor: r => r.description ?? "" },
          { key: "updated_at", header: "Last updated", accessor: r => r.updated_at, render: r => new Date(r.updated_at).toLocaleString() },
        ]}
      />
    </div>
  );
}
