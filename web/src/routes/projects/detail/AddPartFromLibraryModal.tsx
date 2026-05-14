import { FormEvent, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ImageOff, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { isSafeHttpOrSameOriginUrl } from "@/lib/url";
import type { Part, ProjectEntry } from "@/types";

type Props = {
  open: boolean;
  projectId: string;
  onClose: () => void;
};

type AddPayload = {
  parts: Part[];
};

function partSubtitle(part: Part): string {
  return [part.mpn, part.manufacturer].filter(Boolean).join(" - ");
}

export default function AddPartFromLibraryModal({ open, projectId, onClose }: Props) {
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => window.clearTimeout(handle);
  }, [search]);

  const partsQuery = useQuery({
    queryKey: useWsKey("parts", "library-picker", debouncedSearch),
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

  const parts = useMemo(() => partsQuery.data ?? [], [partsQuery.data]);
  const selectedParts = useMemo(
    () => parts.filter(part => selected.has(part.id)),
    [parts, selected],
  );

  useEffect(() => {
    if (!open) return;
    setSelected(prev => {
      if (prev.size === 0) return prev;
      const visible = new Set(parts.map(part => part.id));
      const next = new Set<string>();
      for (const id of prev) if (visible.has(id)) next.add(id);
      return next.size === prev.size ? prev : next;
    });
  }, [open, parts]);

  const addMutation = useApiMutation<ProjectEntry[], AddPayload>({
    mutationKey: ["project", projectId, "add-library-parts"],
    mutationFn: async ({ parts: picked }) => {
      const created: ProjectEntry[] = [];
      for (const part of picked) {
        created.push(
          await api.post<ProjectEntry>(`/projects/${projectId}/entries`, {
            entry_type: "part",
            part_id: part.id,
            name: part.name,
            quantity: 1,
            designators: [],
            dnp: false,
          }),
        );
      }
      return created;
    },
    onSuccess: created => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "project", projectId, "entries") });
      setSelected(new Set());
      toast.success(`Added ${created.length} part${created.length === 1 ? "" : "s"} to BOM.`);
      onClose();
    },
    onError: err => {
      setError(err instanceof ApiError ? err.userMessage : "Failed to add parts to BOM");
    },
  });

  if (!open) return null;

  function toggle(partId: string) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(partId)) next.delete(partId);
      else next.add(partId);
      return next;
    });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (selectedParts.length === 0) {
      setError("Select at least one part.");
      return;
    }
    addMutation.mutate({ parts: selectedParts });
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-library-part-title"
      onMouseDown={event => {
        if (event.target === event.currentTarget && !addMutation.isPending) onClose();
      }}
    >
      <form className="card w-full max-w-2xl p-4 shadow-lg" onSubmit={submit}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="add-library-part-title" className="text-base font-semibold text-text">
              Add part from library
            </h2>
            <p className="mt-1 text-sm text-muted">Choose existing workspace parts for this BOM.</p>
          </div>
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={onClose}
            disabled={addMutation.isPending}
          >
            Close
          </button>
        </div>

        {error && (
          <div className="mt-3 card p-3 text-sm text-danger" role="alert">
            {error}
          </div>
        )}

        <div className="mt-4 space-y-3">
          <label className="label" htmlFor="library-part-search">
            Search library
            <input
              id="library-part-search"
              className="input"
              placeholder="Search MPN or name..."
              value={search}
              onChange={event => setSearch(event.currentTarget.value)}
              disabled={addMutation.isPending}
              autoFocus
            />
          </label>

          <div className="max-h-96 overflow-auto rounded border border-border">
            {partsQuery.isLoading ? (
              <div className="flex items-center gap-2 p-4 text-sm text-muted">
                <Loader2 size={14} className="animate-spin" />
                Loading parts...
              </div>
            ) : parts.length === 0 ? (
              <div className="p-4 text-sm text-muted">
                {debouncedSearch ? "No parts match that search." : "No library parts found."}
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {parts.map(part => {
                  const checked = selected.has(part.id);
                  const safeImageUrl = isSafeHttpOrSameOriginUrl(part.image_url) ? part.image_url : null;
                  return (
                    <li key={part.id}>
                      <label className="flex cursor-pointer items-center gap-3 p-3 hover:bg-panel2/40">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggle(part.id)}
                          disabled={addMutation.isPending}
                          aria-label={checked ? `Deselect ${part.name}` : `Select ${part.name}`}
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
                          <span className="block truncate text-sm font-medium text-text">{part.name}</span>
                          <span className="block truncate text-xs text-muted">{partSubtitle(part) || "No MPN"}</span>
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            className="btn"
            onClick={onClose}
            disabled={addMutation.isPending}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="btn-primary"
            disabled={addMutation.isPending || selectedParts.length === 0}
          >
            {addMutation.isPending ? "Adding..." : `Add ${selectedParts.length} part${selectedParts.length === 1 ? "" : "s"} to BOM`}
          </button>
        </div>
      </form>
    </div>
  );
}
