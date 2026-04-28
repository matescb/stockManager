import { useNavigate, useOutletContext, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Part } from "@/types";

export default function PartOther() {
  const { part } = useOutletContext<{ part: Part }>();
  const { partId } = useParams();
  const qc = useQueryClient();
  const nav = useNavigate();

  async function archive() {
    await api.post(`/parts/${partId}/archive`);
    qc.invalidateQueries();
    nav("/parts");
  }
  async function restore() {
    await api.post(`/parts/${partId}/restore`);
    qc.invalidateQueries();
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
