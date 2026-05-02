import { useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderKanban } from "lucide-react";
import { api } from "@/lib/api";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import type { Part, ProjectEntry } from "@/types";
import { useState } from "react";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";

export default function ProjectBOM() {
  const { projectId } = useParams();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const { data: entries } = useQuery({
    queryKey: useWsKey("project", projectId, "entries"),
    queryFn: () => api.get<ProjectEntry[]>(`/projects/${projectId}/entries`),
  });
  const { data: parts } = useQuery({ queryKey: useWsKey("parts"), queryFn: () => api.get<Part[]>("/parts") });
  const partsById = new Map(parts?.map(p => [p.id, p]) ?? []);

  const [matching, setMatching] = useState<{ entryId: string; pick: string } | null>(null);

  async function match(entryId: string, partId: string) {
    await api.post(`/projects/${projectId}/entries/${entryId}/match`, { part_id: partId });
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "project", projectId, "entries") });
    setMatching(null);
  }

  async function delEntry(entryId: string) {
    await api.delete(`/projects/${projectId}/entries/${entryId}`);
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "project", projectId, "entries") });
  }

  return (
    <div>
      <DataTable
        rows={entries ?? []}
        rowKey={r => r.id}
        tableId="project-bom"
        empty={
          <EmptyState
            icon={FolderKanban}
            title="BOM is empty"
            description="Use the Import BOM tab to load a CSV/TSV."
          />
        }
        exportFilename="bom"
        columns={[
          { key: "qty", header: "Qty", accessor: r => r.quantity, width: "60px" },
          {
            key: "part",
            header: "Part",
            accessor: r => (r.part_id ? (partsById.get(r.part_id)?.name ?? "") : r.name ?? ""),
            render: r =>
              r.entry_type === "part" && r.part_id ? (
                <span className="text-accent">{partsById.get(r.part_id)?.name ?? r.part_id}</span>
              ) : r.entry_type === "unmatched" ? (
                matching?.entryId === r.id ? (
                  <span className="flex gap-1">
                    <select className="input" value={matching.pick} onChange={e => setMatching({ entryId: r.id, pick: e.target.value })}>
                      <option value="">— pick a part —</option>
                      {parts?.map(p => <option key={p.id} value={p.id}>{p.name}{p.mpn ? ` — ${p.mpn}` : ""}</option>)}
                    </select>
                    <button className="btn" onClick={() => matching.pick && match(r.id, matching.pick)}>Match</button>
                    <button className="btn" onClick={() => setMatching(null)}>Cancel</button>
                  </span>
                ) : (
                  <span>
                    <span className="pill bg-danger/20 text-danger mr-2">unmatched</span>
                    <span>{r.name || ""}</span>
                    <button className="btn ml-2 text-xs" onClick={() => setMatching({ entryId: r.id, pick: "" })}>Match…</button>
                  </span>
                )
              ) : (
                <span>{r.name}</span>
              ),
          },
          { key: "designators", header: "Designators", accessor: r => (r.designators ?? []).join(", ") },
          { key: "comments", header: "Comments", accessor: r => r.comments ?? "" },
          { key: "dnp", header: "DNP", accessor: r => r.dnp ? "yes" : "" },
          {
            key: "actions",
            header: "",
            accessor: () => "",
            render: r => <button className="btn-danger text-xs" onClick={() => delEntry(r.id)}>Delete</button>,
          },
        ]}
      />
    </div>
  );
}
