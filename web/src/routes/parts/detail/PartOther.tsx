import { useNavigate, useOutletContext, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { wsScope } from "@/lib/queryKeys";
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
  async function archive() {
    await api.post(`/parts/${partId}/archive`);
    qc.invalidateQueries({ queryKey: wsScope(workspaceId) });
    nav("/parts");
  }
  async function restore() {
    await api.post(`/parts/${partId}/restore`);
    qc.invalidateQueries({ queryKey: wsScope(workspaceId) });
  }

  return (
    <div className="card p-4 max-w-xl space-y-3">
      <h3 className="text-md font-semibold">Other operations</h3>
      {part.archived_at ? (
        <button className="btn" onClick={restore}>Restore from archive</button>
      ) : (
        <button className="btn-danger" onClick={archive}>Archive part</button>
      )}
    </div>
  );
}
