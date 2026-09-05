import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";

type StorageCreatePayload = {
  name: string;
  description: string;
  single_part_only: boolean;
  existing_parts_only: boolean;
  is_full: boolean;
};

export default function StorageCreate() {
  const nav = useNavigate();
  const [form, setForm] = useState<StorageCreatePayload>({
    name: "",
    description: "",
    single_part_only: false,
    existing_parts_only: false,
    is_full: false,
  });
  const [err, setErr] = useState<string | null>(null);

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm(f => ({ ...f, [k]: v }));
  }

  const createMutation = useApiMutation<{ id: string }, StorageCreatePayload>({
    mutationKey: ["storage", "create"],
    mutationFn: (payload) => api.post<{ id: string }>("/storage", payload),
    onSuccess: (res) => {
      nav(`/storage/${res.id}/info`);
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.userMessage : "Failed");
    },
  });

  const busy = createMutation.isPending;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    createMutation.mutate(form);
  }

  return (
    <form onSubmit={submit} className="card p-4 max-w-xl space-y-3">
      <h1 className="page-title">Create storage location</h1>
      {err && <div className="text-danger text-sm">{err}</div>}
      <div>
        <label className="label" htmlFor="storage-create-name">Name *</label>
        <input id="storage-create-name" className="input" required value={form.name} onChange={e => set("name", e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="storage-create-description">Description</label>
        <textarea id="storage-create-description" className="input" rows={3} value={form.description} onChange={e => set("description", e.target.value)} />
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form.single_part_only} onChange={e => set("single_part_only", e.target.checked)} />
        Limit to a single part
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form.existing_parts_only} onChange={e => set("existing_parts_only", e.target.checked)} />
        Only allow existing parts
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form.is_full} onChange={e => set("is_full", e.target.checked)} />
        Mark as full
      </label>
      <div className="flex gap-2">
        <button className="btn-primary" disabled={busy}>{busy ? "Creating…" : "Create"}</button>
        <button type="button" className="btn" onClick={() => nav("/storage")}>Cancel</button>
      </div>
    </form>
  );
}
