import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderKanban, ImageOff, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import type { Part, ProjectEntry } from "@/types";
import { useState } from "react";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import QueryStateBoundary from "@/components/QueryStateBoundary";
import AddPartFromLibraryModal from "./AddPartFromLibraryModal";
import { SourceBomButton } from "@/routes/projects/sourcing/SourceBomButton";

export default function ProjectBOM() {
  const { projectId } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const entriesQuery = useQuery({
    queryKey: useWsKey("project", projectId, "entries"),
    queryFn: () => api.get<ProjectEntry[]>(`/projects/${projectId}/entries`),
  });
  const { data: entries } = entriesQuery;
  const { data: parts } = useQuery({ queryKey: useWsKey("parts"), queryFn: () => api.get<Part[]>("/parts?limit=200") });
  const partsById = new Map(parts?.map(p => [p.id, p]) ?? []);

  const [matching, setMatching] = useState<{ entryId: string; pick: string } | null>(null);
  const [addPartOpen, setAddPartOpen] = useState(false);

  const bulkDeleteMutation = useApiMutation<null[], string[]>({
    mutationKey: ["project", projectId, "bulk-delete-entries"],
    mutationFn: async entryIds => {
      const deleted: null[] = [];
      for (const entryId of entryIds) {
        deleted.push(await api.delete<null>(`/projects/${projectId}/entries/${entryId}`));
      }
      return deleted;
    },
    onSuccess: (_res, entryIds) => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "project", projectId, "entries") });
      toast.success(`Deleted ${entryIds.length} BOM row${entryIds.length === 1 ? "" : "s"}.`);
    },
  });

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
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <SourceBomButton projectId={projectId} className="btn" />
        {projectId && (
          <Link className="btn" to={`/projects/${projectId}/import`}>
            Import BOM
          </Link>
        )}
        <button
          type="button"
          className="btn"
          onClick={() => setAddPartOpen(true)}
          disabled={!projectId}
        >
          Add Part
        </button>
      </div>
      <QueryStateBoundary query={entriesQuery} resourceLabel="BOM entries">
      <DataTable
        rows={entries ?? []}
        rowKey={r => r.id}
        tableId="project-bom"
        searchPlaceholder="Search BOM..."
        selectable
        selectionAccessory={(ids, clear) => (
          <button
            type="button"
            className="btn-danger inline-flex items-center gap-1.5"
            disabled={bulkDeleteMutation.isPending}
            onClick={() => {
              clear();
              bulkDeleteMutation.mutate(ids);
            }}
          >
            <Trash2 size={14} />
            Delete ({ids.length})
          </button>
        )}
        empty={
          <EmptyState
            icon={FolderKanban}
            title="BOM is empty"
            description="Use Import BOM or Add Part to populate this project."
          />
        }
        exportFilename="bom"
        onRowClick={r => {
          if (r.part_id) nav(`/parts/${r.part_id}/info`);
        }}
        rowCanClick={r => r.entry_type === "part" && !!r.part_id}
        rowClassName={r => r.entry_type === "unmatched" || !r.part_id ? "text-muted" : undefined}
        columns={[
          {
            key: "image",
            header: "",
            width: "44px",
            render: r => {
              const part = r.part_id ? partsById.get(r.part_id) : null;
              return part?.image_url ? (
                <img
                  src={part.image_url}
                  alt=""
                  loading="lazy"
                  className="h-8 w-8 rounded bg-panel object-contain"
                />
              ) : (
                <span className="flex h-8 w-8 items-center justify-center rounded bg-panel2/40 text-muted">
                  <ImageOff size={14} />
                </span>
              );
            },
          },
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
          { key: "mpn", header: "MPN", accessor: r => r.part_id ? partsById.get(r.part_id)?.mpn ?? "" : "" },
          { key: "manufacturer", header: "Manufacturer", accessor: r => r.part_id ? partsById.get(r.part_id)?.manufacturer ?? "" : "" },
          { key: "qty", header: "Qty", accessor: r => r.quantity, width: "70px" },
          { key: "designators", header: "Designators", accessor: r => (r.designators ?? []).join(", ") },
          {
            key: "status",
            header: "Status",
            accessor: r => r.entry_type === "part" && r.part_id ? "matched" : "unmatched",
            render: r => r.entry_type === "part" && r.part_id ? (
              <span className="pill bg-accent/15 text-accent">matched</span>
            ) : (
              <span className="pill bg-danger/20 text-danger">unmatched</span>
            ),
            width: "110px",
          },
          {
            key: "actions",
            header: "",
            accessor: () => "",
            render: r => (
              <button
                className="btn-danger text-xs"
                onClick={event => {
                  event.stopPropagation();
                  delEntry(r.id);
                }}
              >
                Delete
              </button>
            ),
          },
        ]}
      />
      </QueryStateBoundary>
      {projectId && (
        <AddPartFromLibraryModal
          open={addPartOpen}
          projectId={projectId}
          onClose={() => setAddPartOpen(false)}
        />
      )}
    </div>
  );
}
