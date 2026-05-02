import { useNavigate, useOutletContext } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { archiveProjectKeys } from "@/lib/queryKeys";
import type { Project } from "@/types";

export default function ProjectOther() {
  const { project } = useOutletContext<{ project: Project }>();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const nav = useNavigate();
  async function arch() {
    await api.post(`/projects/${project.id}/archive`);
    for (const k of archiveProjectKeys(workspaceId, project.id))
      qc.invalidateQueries({ queryKey: k });
    nav("/projects");
  }
  async function restore() {
    await api.post(`/projects/${project.id}/restore`);
    for (const k of archiveProjectKeys(workspaceId, project.id))
      qc.invalidateQueries({ queryKey: k });
  }
  return (
    <div className="card p-4 max-w-xl">
      {project.archived_at ? (
        <button className="btn" onClick={restore}>Restore</button>
      ) : (
        <button className="btn-danger" onClick={arch}>Archive project</button>
      )}
    </div>
  );
}
