import { useNavigate, useOutletContext } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useAuth } from "@/lib/auth";
import { archiveProjectKeys } from "@/lib/queryKeys";
import type { Project } from "@/types";

export default function ProjectOther() {
  const { project } = useOutletContext<{ project: Project }>();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const nav = useNavigate();

  const archiveMutation = useApiMutation<unknown, { wasArchived: boolean }>({
    mutationKey: ["project", project.id, "archive"],
    mutationFn: ({ wasArchived }) =>
      api.post(`/projects/${project.id}/${wasArchived ? "restore" : "archive"}`),
    onSuccess: (_data, vars) => {
      for (const k of archiveProjectKeys(workspaceId, project.id))
        qc.invalidateQueries({ queryKey: k });
      toast.success(vars.wasArchived ? "Project restored." : "Project archived.");
      if (!vars.wasArchived) nav("/projects");
    },
    onError: (e) => {
      toast.error(e.userMessage);
    },
  });

  function arch() {
    archiveMutation.mutate({ wasArchived: false });
  }
  function restore() {
    archiveMutation.mutate({ wasArchived: true });
  }
  return (
    <div className="card p-4 max-w-xl">
      {project.archived_at ? (
        <button className="btn" onClick={restore} disabled={archiveMutation.isPending}>Restore</button>
      ) : (
        <button className="btn-danger" onClick={arch} disabled={archiveMutation.isPending}>Archive project</button>
      )}
    </div>
  );
}
