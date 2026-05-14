import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CloudDownload, FolderKanban, ImageOff, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import { isSafeHttpOrSameOriginUrl } from "@/lib/url";
import type { Part, ProjectEntry } from "@/types";
import { useState } from "react";
import { DataTable } from "@/components/DataTable";
import EmptyState from "@/components/EmptyState";
import QueryStateBoundary from "@/components/QueryStateBoundary";
import AddPartFromLibraryModal from "./AddPartFromLibraryModal";
import BomProviderAmbiguityModal, { type BomProviderPendingChoice } from "./BomProviderAmbiguityModal";
import BomProviderFailuresPanel, { type BomProviderFailure } from "./BomProviderFailuresPanel";
import { SourceBomButton } from "@/routes/projects/sourcing/SourceBomButton";

type WorkspaceProviderSettings = {
  parts_provider: "none" | "mouser" | "digikey";
};

type BomProviderImportOut = {
  created: number;
  linked_existing: number;
  pending_choices: BomProviderPendingChoice[];
  failures: BomProviderFailure[];
  provider: WorkspaceProviderSettings["parts_provider"];
  truncated: boolean;
};

type BomProviderImportPayload = {
  entry_ids: string[] | null;
};

const PROVIDER_LABEL: Record<string, string> = {
  none: "provider",
  mouser: "Mouser",
  digikey: "DigiKey",
};

export default function ProjectBOM() {
  const { projectId } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const entriesQuery = useQuery({
    queryKey: useWsKey("project", projectId, "entries"),
    queryFn: ({ signal }) => api.get<ProjectEntry[]>(`/projects/${projectId}/entries`, { signal }),
  });
  const { data: entries } = entriesQuery;
  const { data: parts } = useQuery({ queryKey: useWsKey("parts"), queryFn: ({ signal }) => api.get<Part[]>("/parts?limit=200", { signal }) });
  const { data: workspace } = useQuery({
    queryKey: useWsKey("ws", "current"),
    queryFn: ({ signal }) => api.get<WorkspaceProviderSettings>("/workspaces/current", { signal }),
  });
  const partsById = new Map(parts?.map(p => [p.id, p]) ?? []);

  const [matching, setMatching] = useState<{ entryId: string; pick: string } | null>(null);
  const [addPartOpen, setAddPartOpen] = useState(false);
  const [pendingChoices, setPendingChoices] = useState<BomProviderPendingChoice[]>([]);
  const [failures, setFailures] = useState<BomProviderFailure[]>([]);

  const provider = workspace?.parts_provider ?? "none";
  const providerLabel = PROVIDER_LABEL[provider] ?? provider;
  const unmatchedEntries = (entries ?? []).filter(entry => entry.entry_type === "unmatched" && !entry.part_id);

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

  function handleProviderResult(result: BomProviderImportOut) {
    if (result.created > 0 || result.linked_existing > 0) {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "project", projectId, "entries") });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "parts") });
      const providerName = PROVIDER_LABEL[result.provider] ?? result.provider;
      const messages: string[] = [];
      if (result.created > 0) {
        messages.push(`Created ${result.created} part${result.created === 1 ? "" : "s"}`);
      }
      if (result.linked_existing > 0) {
        messages.push(
          `${result.created > 0 ? "linked" : "Linked"} ${result.linked_existing} existing part${result.linked_existing === 1 ? "" : "s"}`,
        );
      }
      toast.success(`${messages.join(" and ")} from ${providerName}.`);
    }
    if (result.pending_choices.length > 0) {
      setPendingChoices(result.pending_choices);
    }
    if (result.failures.length > 0) {
      setFailures(result.failures);
    }
    if (result.truncated) {
      toast.error("Imported first 200 unmatched rows. Run provider import again for remaining rows.");
    }
  }

  const importProviderMutation = useApiMutation<BomProviderImportOut, BomProviderImportPayload>({
    mutationKey: ["project", projectId, "bom-import-provider"],
    mutationFn: payload => api.post<BomProviderImportOut, BomProviderImportPayload>(`/projects/${projectId}/bom/import-from-provider`, payload),
    onSuccess: handleProviderResult,
    onError: error => {
      toast.error(error instanceof ApiError ? error.userMessage : "Provider import failed");
    },
  });

  const commitChoicesMutation = useApiMutation<BomProviderImportOut, { choices: Record<string, string> }>({
    mutationKey: ["project", projectId, "bom-import-provider-choices"],
    mutationFn: payload => api.post<BomProviderImportOut, { choices: Record<string, string> }>(`/projects/${projectId}/bom/import-from-provider/commit-choices`, payload),
    onSuccess: result => {
      setPendingChoices([]);
      handleProviderResult(result);
    },
    onError: error => {
      toast.error(error instanceof ApiError ? error.userMessage : "Provider import failed");
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
        {projectId && provider !== "none" && unmatchedEntries.length > 0 && (
          <button
            type="button"
            className="btn inline-flex items-center gap-1.5"
            disabled={importProviderMutation.isPending}
            onClick={() => importProviderMutation.mutate({ entry_ids: null })}
          >
            {importProviderMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <CloudDownload size={14} />}
            Import all unmatched from {providerLabel}
          </button>
        )}
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
              const imageUrl = part?.image_url;
              const safeImageUrl = isSafeHttpOrSameOriginUrl(imageUrl) ? imageUrl : null;
              return safeImageUrl ? (
                <img
                  src={safeImageUrl}
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
                    {provider !== "none" && (
                      <button
                        className="btn ml-2 text-xs"
                        disabled={importProviderMutation.isPending}
                        onClick={event => {
                          event.stopPropagation();
                          importProviderMutation.mutate({ entry_ids: [r.id] });
                        }}
                      >
                        Import from provider
                      </button>
                    )}
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
      <BomProviderAmbiguityModal
        open={pendingChoices.length > 0}
        choices={pendingChoices}
        busy={commitChoicesMutation.isPending}
        onClose={() => setPendingChoices([])}
        onConfirm={choices => commitChoicesMutation.mutate({ choices })}
      />
      <BomProviderFailuresPanel failures={failures} onClose={() => setFailures([])} />
    </div>
  );
}
