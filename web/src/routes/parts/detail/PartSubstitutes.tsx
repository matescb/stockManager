import { useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import type { Part } from "@/types";

type Sub = { part_id: string; direction: string };

export default function PartSubstitutes() {
  const { partId } = useParams();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const { data: subs } = useQuery({ queryKey: useWsKey("part", partId, "subs"), queryFn: () => api.get<Sub[]>(`/parts/${partId}/substitutes`) });
  const { data: parts } = useQuery({ queryKey: useWsKey("parts"), queryFn: () => api.get<Part[]>("/parts") });
  const partsById = new Map(parts?.map(p => [p.id, p]) ?? []);
  const [pick, setPick] = useState("");

  async function add() {
    if (!pick) return;
    await api.post(`/parts/${partId}/substitutes`, { substitute_part_id: pick });
    setPick("");
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", partId, "subs") });
  }
  async function remove(id: string) {
    await api.delete(`/parts/${partId}/substitutes/${id}`);
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", partId, "subs") });
  }

  return (
    <div className="card p-4 max-w-2xl">
      <h3 className="text-md font-semibold mb-2">Substitutes</h3>
      <ul className="space-y-1 mb-3">
        {(subs ?? []).map(s => {
          const p = partsById.get(s.part_id);
          return (
            <li key={s.part_id} className="text-sm flex items-center justify-between">
              <span>{p?.name ?? s.part_id}{p?.mpn && <span className="text-muted ml-2">{p.mpn}</span>}</span>
              <button className="btn-danger text-xs" onClick={() => remove(s.part_id)}>Remove</button>
            </li>
          );
        })}
        {(!subs || subs.length === 0) && <li className="text-muted text-sm">No substitutes.</li>}
      </ul>
      <div className="flex gap-2">
        <select className="input" value={pick} onChange={e => setPick(e.target.value)}>
          <option value="">Pick a part…</option>
          {parts?.filter(p => p.id !== partId).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button className="btn-primary" onClick={add}>Add</button>
      </div>
    </div>
  );
}
