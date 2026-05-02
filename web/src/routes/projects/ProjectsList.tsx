import { Link, NavLink, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FolderKanban } from "lucide-react";
import { api } from "@/lib/api";
import { wsKey } from "@/lib/queryKeys";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import QueryStateBoundary from "@/components/QueryStateBoundary";
import type { Project } from "@/types";

export default function ProjectsList({ archived = false }: { archived?: boolean }) {
  const nav = useNavigate();
  const query = useQuery({
    queryKey: wsKey("projects", { archived }),
    queryFn: () => api.get<Project[]>(`/projects${archived ? "?archived=true" : ""}`),
  });
  const { data } = query;
  return (
    <div>
      <div className="flex items-center gap-1 mb-3">
        <NavLink to="/projects" end className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Projects</NavLink>
        <NavLink to="/projects/archived" className={({ isActive }) => "btn " + (isActive ? "border-accent/50 text-accent" : "")}>Archived</NavLink>
        <Link to="/projects/create" className="btn-primary ml-auto">+ Project</Link>
      </div>
      <QueryStateBoundary query={query} resourceLabel="projects">
      <DataTable
        rows={data ?? []}
        rowKey={r => r.id}
        tableId="projects"
        empty={
          archived ? (
            <EmptyState
              icon={FolderKanban}
              title="No archived projects"
              description="Archived projects will appear here."
            />
          ) : (
            <EmptyState
              icon={FolderKanban}
              title="No projects yet"
              description="Create a project to track its BOM and builds."
              action={{ label: "+ Project", to: "/projects/create" }}
            />
          )
        }
        exportFilename="projects"
        onRowClick={r => nav(`/projects/${r.id}/data`)}
        columns={[
          { key: "name", header: "Name", accessor: r => r.name },
          { key: "description", header: "Description", accessor: r => r.description ?? "" },
          { key: "updated_at", header: "Last updated", accessor: r => r.updated_at, render: r => new Date(r.updated_at).toLocaleString() },
        ]}
      />
      </QueryStateBoundary>
    </div>
  );
}
