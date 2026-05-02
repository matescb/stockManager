import { Outlet, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import EntityHeader from "@/components/EntityHeader";
import SubNav from "@/components/SubNav";
import type { Project } from "@/types";

export default function ProjectLayout() {
  const { projectId } = useParams<{ projectId: string }>();
  const { data } = useQuery({ queryKey: useWsKey("project", projectId), queryFn: () => api.get<Project>(`/projects/${projectId}`), enabled: !!projectId });
  if (!data) return <div className="text-muted">Loading…</div>;
  const items = [
    { to: `/projects/${data.id}/data`, label: "Project info" },
    { to: `/projects/${data.id}/bom`, label: "BOM" },
    { to: `/projects/${data.id}/import`, label: "Import BOM" },
    { to: `/projects/${data.id}/builds`, label: "Builds" },
    { to: `/projects/${data.id}/other`, label: "Other" },
  ];
  return (
    <div>
      <EntityHeader title={data.name} subtitle={data.description ?? ""} idCode={data.id} />
      <SubNav items={items} />
      <Outlet key={data.id} context={{ project: data }} />
    </div>
  );
}
