import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ImageOff, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Modal } from "@/components/Modal";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { isSafeHttpOrSameOriginUrl } from "@/lib/url";
import type { Part, Project } from "@/types";

type Props = {
  open: boolean;
  /** The part being replaced (the source of the operation). */
  part: Part;
  onClose: () => void;
};

type ReplaceResult = {
  updated_entries: number;
  affected_projects: number;
};

type ReplacePayload = {
  target_part_id: string;
  project_ids?: string[];
};

function partSubtitle(part: Part): string {
  return [part.mpn, part.manufacturer].filter(Boolean).join(" - ");
}

export default function ReplaceInProjectsModal({ open, part, onClose }: Props) {
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const partId = part.id;

  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [targetId, setTargetId] = useState<string | null>(null);
  const [allProjects, setAllProjects] = useState(true);
  const [selectedProjects, setSelectedProjects] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  // Reset transient state each time the modal opens so a re-open never
  // shows a stale selection from a previous run.
  useEffect(() => {
    if (!open) return;
    setSearch("");
    setDebouncedSearch("");
    setTargetId(null);
    setAllProjects(true);
    setSelectedProjects(new Set());
    setError(null);
  }, [open]);

  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => window.clearTimeout(handle);
  }, [search]);

  const partsQuery = useQuery({
    queryKey: useWsKey("parts", "replace-picker", debouncedSearch),
    queryFn: ({ signal }) => {
      const params = new URLSearchParams({ limit: "20" });
      if (debouncedSearch) {
        params.set("q", debouncedSearch);
        params.set("search", debouncedSearch);
      }
      return api.get<Part[]>(`/parts?${params.toString()}`, { signal });
    },
    enabled: open,
  });

  const projectsQuery = useQuery({
    queryKey: useWsKey("projects", "replace-picker"),
    queryFn: ({ signal }) => api.get<Project[]>("/projects?limit=1000", { signal }),
    enabled: open,
  });

  // Never offer the part being replaced as its own replacement — the API
  // rejects target == source with a 400.
  const candidateParts = useMemo(
    () => (partsQuery.data ?? []).filter(p => p.id !== partId),
    [partsQuery.data, partId],
  );
  const projects = useMemo(() => projectsQuery.data ?? [], [projectsQuery.data]);

  const replaceMutation = useApiMutation<ReplaceResult, ReplacePayload>({
    mutationKey: ["part", partId, "replace-in-projects"],
    mutationFn: payload =>
      api.post<ReplaceResult, ReplacePayload>(`/parts/${partId}/replace-in-projects`, payload),
    onSuccess: result => {
      // Both projects (BOM lines changed) and parts (the source part's
      // activity / usage) can be stale after a bulk repoint.
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "projects") });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "project") });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "parts") });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", partId) });
      const { updated_entries, affected_projects } = result;
      if (updated_entries === 0) {
        toast.info("No BOM lines used this part in the selected projects.");
      } else {
        toast.success(
          `Replaced ${updated_entries} BOM line${updated_entries === 1 ? "" : "s"} across ` +
            `${affected_projects} project${affected_projects === 1 ? "" : "s"}.`,
        );
      }
      onClose();
    },
    onError: err => {
      setError(err instanceof ApiError ? err.userMessage : "Failed to replace part");
    },
  });

  // A dismiss must not strand an in-flight replace, so the focus-trap's own
  // exits (Escape, backdrop) obey the same guard the Cancel button does.
  const closeUnlessBusy = useCallback(() => {
    if (!replaceMutation.isPending) onClose();
  }, [replaceMutation.isPending, onClose]);

  if (!open) return null;

  function toggleProject(projectId: string) {
    setSelectedProjects(prev => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!targetId) {
      setError("Choose a replacement part.");
      return;
    }
    if (!allProjects && selectedProjects.size === 0) {
      setError("Select at least one project, or replace across all projects.");
      return;
    }
    const payload: ReplacePayload = { target_part_id: targetId };
    if (!allProjects) payload.project_ids = Array.from(selectedProjects);
    replaceMutation.mutate(payload);
  }

  const busy = replaceMutation.isPending;

  return (
    <Modal
      open={open}
      onClose={closeUnlessBusy}
      title="Replace in projects"
      className="card w-full max-w-2xl shadow-lg"
      initialFocusRef={searchRef}
    >
      <form className="p-4" onSubmit={submit}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="card-title text-text">
              Replace in projects
            </h2>
            <p className="mt-1 text-sm text-muted">
              Repoint every BOM line using <span className="font-medium text-text">{part.name}</span> to a
              replacement part across your projects.
            </p>
          </div>
          <button type="button" className="btn-ghost btn-sm" onClick={onClose} disabled={busy}>
            Close
          </button>
        </div>

        {error && (
          <div className="mt-3 card p-3 text-sm text-danger" role="alert">
            {error}
          </div>
        )}

        <div className="mt-4 space-y-4">
          {/* Replacement part picker */}
          <div className="space-y-2">
            <label className="label" htmlFor="replace-part-search">
              Replacement part
              <input
                id="replace-part-search"
                ref={searchRef}
                className="input"
                placeholder="Search MPN or name..."
                value={search}
                onChange={event => setSearch(event.currentTarget.value)}
                disabled={busy}
              />
            </label>

            <div className="max-h-60 overflow-auto rounded border border-border">
              {partsQuery.isLoading ? (
                <div className="flex items-center gap-2 p-4 text-sm text-muted">
                  <Loader2 size={14} className="animate-spin" />
                  Loading parts...
                </div>
              ) : candidateParts.length === 0 ? (
                <div className="p-4 text-sm text-muted">
                  {debouncedSearch ? "No parts match that search." : "No other parts found."}
                </div>
              ) : (
                <ul className="divide-y divide-border">
                  {candidateParts.map(candidate => {
                    const checked = targetId === candidate.id;
                    const safeImageUrl = isSafeHttpOrSameOriginUrl(candidate.image_url)
                      ? candidate.image_url
                      : null;
                    return (
                      <li key={candidate.id}>
                        <label className="flex cursor-pointer items-center gap-3 p-3 hover:bg-panel2/40">
                          <input
                            type="radio"
                            name="replace-target"
                            checked={checked}
                            onChange={() => setTargetId(candidate.id)}
                            disabled={busy}
                            aria-label={`Use ${candidate.name} as replacement`}
                          />
                          {safeImageUrl ? (
                            <img
                              src={safeImageUrl}
                              alt=""
                              loading="lazy"
                              className="h-10 w-10 rounded bg-panel object-contain"
                            />
                          ) : (
                            <span className="flex h-10 w-10 items-center justify-center rounded bg-panel2/40 text-muted">
                              <ImageOff size={16} />
                            </span>
                          )}
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium text-text">
                              {candidate.name}
                            </span>
                            <span className="block truncate text-xs text-muted">
                              {partSubtitle(candidate) || "No MPN"}
                            </span>
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>

          {/* Project scope */}
          <div className="space-y-2">
            <span className="label">Projects</span>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={allProjects}
                onChange={() => setAllProjects(v => !v)}
                disabled={busy}
              />
              Replace across all projects
            </label>

            {!allProjects && (
              <div className="max-h-48 overflow-auto rounded border border-border">
                {projectsQuery.isLoading ? (
                  <div className="flex items-center gap-2 p-4 text-sm text-muted">
                    <Loader2 size={14} className="animate-spin" />
                    Loading projects...
                  </div>
                ) : projects.length === 0 ? (
                  <div className="p-4 text-sm text-muted">No projects found.</div>
                ) : (
                  <ul className="divide-y divide-border">
                    {projects.map(project => (
                      <li key={project.id}>
                        <label className="flex cursor-pointer items-center gap-3 p-3 hover:bg-panel2/40">
                          <input
                            type="checkbox"
                            checked={selectedProjects.has(project.id)}
                            onChange={() => toggleProject(project.id)}
                            disabled={busy}
                            aria-label={`Include project ${project.name}`}
                          />
                          <span className="min-w-0 flex-1 truncate text-sm text-text">
                            {project.name}
                          </span>
                        </label>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={busy || !targetId}>
            {busy ? "Replacing..." : "Replace part"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
