import { useNavigate, useOutletContext, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useAuth } from "@/lib/auth";
import { archivePartKeys } from "@/lib/queryKeys";
import type { Part } from "@/types";

export default function PartOther() {
  const { part } = useOutletContext<{ part: Part }>();
  const { partId } = useParams();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const nav = useNavigate();

  // Bare `qc.invalidateQueries()` (no key) used to nuke the entire
  // cache, including queries for unrelated workspaces and any in-flight
  // background data. We now scope invalidation to the active
  // workspace's prefix so we keep the workspace-keyed cache useful and
  // don't blow away unrelated data.
  const archiveMutation = useApiMutation<unknown, { wasArchived: boolean }>({
    mutationKey: ["part", partId, "archive"],
    mutationFn: ({ wasArchived }) =>
      api.post(`/parts/${partId}/${wasArchived ? "restore" : "archive"}`),
    onSuccess: (_data, vars) => {
      for (const k of archivePartKeys(workspaceId, partId!))
        qc.invalidateQueries({ queryKey: k });
      toast.success(vars.wasArchived ? "Part restored." : "Part archived.");
      if (!vars.wasArchived) nav("/parts");
    },
    onError: (e) => {
      toast.error(e.userMessage);
    },
  });

  function archive() {
    archiveMutation.mutate({ wasArchived: false });
  }
  function restore() {
    archiveMutation.mutate({ wasArchived: true });
  }

  return (
    <div className="card p-4 max-w-xl space-y-3">
      <h3 className="text-md font-semibold">Other operations</h3>
      {part.archived_at ? (
        <button className="btn" onClick={restore} disabled={archiveMutation.isPending}>Restore from archive</button>
      ) : (
        <button className="btn-danger" onClick={archive} disabled={archiveMutation.isPending}>Archive part</button>
      )}
    </div>
  );
}
