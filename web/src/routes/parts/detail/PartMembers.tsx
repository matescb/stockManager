import { useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import QueryStateBoundary from "@/components/QueryStateBoundary";
import type { Part } from "@/types";

type Member = { id: string; member_part_id: string };

export default function PartMembers() {
  const { partId } = useParams();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const membersQuery = useQuery({
    queryKey: useWsKey("part", partId, "members"),
    queryFn: () => api.get<Member[]>(`/parts/${partId}/members`),
  });
  const { data: members } = membersQuery;
  const { data: parts } = useQuery({ queryKey: useWsKey("parts"), queryFn: () => api.get<Part[]>("/parts?limit=200") });
  const partsById = new Map(parts?.map(p => [p.id, p]) ?? []);

  const [pick, setPick] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const addMutation = useApiMutation<unknown, string>({
    mutationKey: ["part", partId, "members", "add"],
    mutationFn: (memberPartId) =>
      api.post(`/parts/${partId}/members`, { member_part_id: memberPartId }),
    onSuccess: () => {
      setPick("");
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", partId, "members") });
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.userMessage : "Failed");
    },
  });

  const removeMutation = useApiMutation<unknown, string>({
    mutationKey: ["part", partId, "members", "remove"],
    mutationFn: (memberPartId) => api.delete(`/parts/${partId}/members/${memberPartId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", partId, "members") });
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.userMessage : "Failed");
    },
  });

  function add() {
    if (!pick) return;
    setErr(null);
    addMutation.mutate(pick);
  }
  function remove(mid: string) {
    setErr(null);
    removeMutation.mutate(mid);
  }

  return (
    <div className="card p-4 max-w-2xl">
      <h3 className="text-md font-semibold mb-2">Meta-part members</h3>
      <QueryStateBoundary query={membersQuery} resourceLabel="members">
      <p className="text-sm text-muted mb-3">
        A meta-part stands for any of its members. When a BOM line uses this meta-part,
        a build can consume from any member's stock.
      </p>
      {err && <div className="text-danger text-sm mb-2">{err}</div>}
      <ul className="space-y-1 mb-3">
        {(members ?? []).map(m => {
          const p = partsById.get(m.member_part_id);
          return (
            <li key={m.id} className="text-sm flex items-center justify-between">
              <span>
                {p?.name ?? m.member_part_id}
                {p?.mpn && <span className="text-muted ml-2">{p.mpn}</span>}
              </span>
              <button
                className="btn-danger text-xs"
                onClick={() => remove(m.member_part_id)}
                disabled={removeMutation.isPending}
              >
                Remove
              </button>
            </li>
          );
        })}
        {(!members || members.length === 0) && <li className="text-muted text-sm">No members yet.</li>}
      </ul>
      <div className="flex gap-2">
        <select className="input" value={pick} onChange={e => setPick(e.target.value)}>
          <option value="">Pick a part…</option>
          {parts?.filter(p => p.id !== partId && p.part_type !== "meta").map(p => (
            <option key={p.id} value={p.id}>{p.name}{p.mpn ? ` — ${p.mpn}` : ""}</option>
          ))}
        </select>
        <button className="btn-primary" onClick={add} disabled={!pick || addMutation.isPending}>Add</button>
      </div>
      </QueryStateBoundary>
    </div>
  );
}
