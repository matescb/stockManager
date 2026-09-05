import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";

export default function ProjectCreate() {
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const createMutation = useApiMutation<{ id: string }, { name: string; description: string | null }>({
    mutationKey: ["project", "create"],
    mutationFn: (payload) => api.post<{ id: string }>("/projects", payload),
    onSuccess: (res) => {
      nav(`/projects/${res.id}/data`);
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.userMessage : "Failed");
    },
  });

  const busy = createMutation.isPending;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    createMutation.mutate({ name, description: description || null });
  }
  return (
    <form onSubmit={submit} className="card p-4 max-w-xl space-y-3">
      <h1 className="page-title">Create project</h1>
      {err && <div className="text-danger text-sm">{err}</div>}
      <div>
        <label className="label" htmlFor="project-create-name">Name *</label>
        <input id="project-create-name" className="input" required value={name} onChange={e => setName(e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="project-create-description">Description</label>
        <textarea id="project-create-description" className="input" rows={3} value={description} onChange={e => setDescription(e.target.value)} />
      </div>
      <div className="flex gap-2">
        <button className="btn-primary" disabled={busy}>Create</button>
        <button type="button" className="btn" onClick={() => nav("/projects")}>Cancel</button>
      </div>
    </form>
  );
}
