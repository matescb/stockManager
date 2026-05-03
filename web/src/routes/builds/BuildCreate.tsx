import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey } from "@/lib/queryKeys";
import { InlineQueryError } from "@/components/QueryStateBoundary";
import type { Build, Project } from "@/types";

type BuildCreatePayload = {
  name: string;
  project_id: string;
  quantity: number;
  comments?: string;
};

export default function BuildCreate() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const projectsQuery = useQuery({
    queryKey: useWsKey("projects"),
    queryFn: () => api.get<Project[]>("/projects"),
  });
  const { data: projects } = projectsQuery;
  const [name, setName] = useState("");
  const [projectId, setProjectId] = useState(params.get("project_id") ?? "");
  const [qty, setQty] = useState(1);
  const [comments, setComments] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const createMutation = useApiMutation<Build, BuildCreatePayload>({
    mutationKey: ["build", "create"],
    mutationFn: (payload) => api.post<Build>("/builds", payload),
    onSuccess: (b) => {
      nav(`/builds/${b.id}`);
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.userMessage : "Failed");
    },
  });

  const busy = createMutation.isPending;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    createMutation.mutate({
      name,
      project_id: projectId,
      quantity: qty,
      comments: comments || undefined,
    });
  }

  return (
    <form onSubmit={submit} className="card p-4 max-w-2xl space-y-3">
      <h3 className="text-md font-semibold">New build</h3>
      {err && <div className="text-danger text-sm">{err}</div>}
      <InlineQueryError query={projectsQuery} label="projects" />
      <div>
        <label className="label" htmlFor="build-create-name">Name *</label>
        <input id="build-create-name" className="input" required value={name} onChange={e => setName(e.target.value)} placeholder="BUILD-2026-001" />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="build-create-project">Project *</label>
          <select id="build-create-project" className="input" required value={projectId} onChange={e => setProjectId(e.target.value)}>
            <option value="">— pick —</option>
            {projects?.filter(p => !p.archived_at).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="build-create-qty">Quantity *</label>
          <input id="build-create-qty" className="input" type="number" min={1} step={1} required value={qty} onChange={e => setQty(Number(e.target.value))} />
        </div>
      </div>
      <div>
        <label className="label" htmlFor="build-create-comments">Comments</label>
        <textarea id="build-create-comments" className="input" rows={2} value={comments} onChange={e => setComments(e.target.value)} />
      </div>
      <div>
        <button className="btn-primary" disabled={busy}>{busy ? "Creating…" : "Create build"}</button>
      </div>
    </form>
  );
}
