import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import type { StorageLocation } from "@/types";

export default function PartCreate() {
  const nav = useNavigate();
  const [form, setForm] = useState({
    part_type: "local" as "linked" | "local" | "meta",
    name: "",
    manufacturer: "",
    mpn: "",
    internal_part_number: "",
    description: "",
    footprint: "",
    default_storage_location_id: "",
  });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { data: storage } = useQuery({ queryKey: ["storage"], queryFn: () => api.get<StorageLocation[]>("/storage") });

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm(f => ({ ...f, [k]: v }));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const payload: any = { ...form };
      if (!payload.default_storage_location_id) delete payload.default_storage_location_id;
      const res = await api.post<{ id: string }>("/parts", payload);
      nav(`/parts/${res.id}/info`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="max-w-2xl card p-4 space-y-3">
      <h1 className="text-xl font-semibold">Create part</h1>
      {err && <div className="text-danger text-sm">{err}</div>}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">Type</label>
          <select className="input" value={form.part_type} onChange={e => set("part_type", e.target.value as any)}>
            <option value="local">Local</option>
            <option value="linked">Linked (MPN)</option>
            <option value="meta">Meta-part</option>
          </select>
        </div>
        <div>
          <label className="label">Footprint</label>
          <input className="input" value={form.footprint} onChange={e => set("footprint", e.target.value)} placeholder="0402, SOIC-8…" />
        </div>
      </div>
      <div>
        <label className="label">Name *</label>
        <input className="input" required value={form.name} onChange={e => set("name", e.target.value)} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">Manufacturer</label>
          <input className="input" value={form.manufacturer} onChange={e => set("manufacturer", e.target.value)} />
        </div>
        <div>
          <label className="label">MPN</label>
          <input className="input" value={form.mpn} onChange={e => set("mpn", e.target.value)} />
        </div>
      </div>
      <div>
        <label className="label">Internal part number</label>
        <input className="input" value={form.internal_part_number} onChange={e => set("internal_part_number", e.target.value)} />
      </div>
      <div>
        <label className="label">Description</label>
        <textarea className="input" rows={3} value={form.description} onChange={e => set("description", e.target.value)} />
      </div>
      <div>
        <label className="label">Default storage location</label>
        <select className="input" value={form.default_storage_location_id} onChange={e => set("default_storage_location_id", e.target.value)}>
          <option value="">— none —</option>
          {storage?.filter(s => !s.archived_at).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>
      <div className="flex gap-2">
        <button className="btn-primary" disabled={busy}>{busy ? "Creating…" : "Create"}</button>
        <button type="button" className="btn" onClick={() => nav("/parts")}>Cancel</button>
      </div>
    </form>
  );
}
