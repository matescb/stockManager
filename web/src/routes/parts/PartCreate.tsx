import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import type { StorageLocation, TrustedPartsResult } from "@/types";
import MpnLookup from "@/components/MpnLookup";

export default function PartCreate() {
  const nav = useNavigate();
  const [form, setForm] = useState({
    part_type: "local" as "linked" | "local" | "meta" | "sub_assembly",
    name: "",
    manufacturer: "",
    mpn: "",
    internal_part_number: "",
    description: "",
    footprint: "",
    default_storage_location_id: "",
    serialized: false,
  });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Datasheet URL discovered via TrustedParts lookup; persisted as a
  // custom_field after the part is created (see submit()).
  const [datasheetUrl, setDatasheetUrl] = useState<string | null>(null);
  const { data: storage } = useQuery({ queryKey: ["storage"], queryFn: () => api.get<StorageLocation[]>("/storage") });

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm(f => ({ ...f, [k]: v }));
  }

  function applyLookup(r: NonNullable<TrustedPartsResult["result"]>) {
    setForm(f => ({
      ...f,
      manufacturer: r.manufacturer ?? f.manufacturer,
      description: r.description ?? f.description,
      footprint: r.footprint ?? f.footprint,
    }));
    setDatasheetUrl(r.datasheet_url ?? null);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const payload: any = { ...form };
      if (!payload.default_storage_location_id) delete payload.default_storage_location_id;
      const res = await api.post<{ id: string }>("/parts", payload);
      // If the lookup found a datasheet URL, store it as a custom_field
      // on the new part. We swallow failures here — the part is already
      // created and the user shouldn't be blocked on a metadata side-effect.
      if (datasheetUrl) {
        try {
          await api.post("/custom-fields", {
            object_type: "part",
            object_id: res.id,
            key: "datasheet_url",
            value: datasheetUrl,
          });
        } catch {
          /* non-fatal */
        }
      }
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
            <option value="sub_assembly">Sub-assembly</option>
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
          <div className="flex items-end gap-2">
            <input className="input flex-1" value={form.mpn} onChange={e => set("mpn", e.target.value)} />
            {form.part_type === "linked" && <MpnLookup mpn={form.mpn} onResult={applyLookup} />}
          </div>
          {datasheetUrl && (
            <div className="text-xs text-muted mt-1">
              Datasheet: <a className="underline" href={datasheetUrl} target="_blank" rel="noreferrer">{datasheetUrl}</a>
            </div>
          )}
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
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form.serialized} onChange={e => set("serialized", e.target.checked)} />
        Serialized (one unit per lot, requires serial number — only enforced when the workspace has serial tracking on)
      </label>
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
