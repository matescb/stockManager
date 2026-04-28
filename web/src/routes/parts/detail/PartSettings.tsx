import { useState } from "react";
import { useOutletContext, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import type { Part, StorageLocation } from "@/types";

export default function PartSettings() {
  const { part } = useOutletContext<{ part: Part }>();
  const { partId } = useParams();
  const qc = useQueryClient();
  const { data: storage } = useQuery({ queryKey: ["storage"], queryFn: () => api.get<StorageLocation[]>("/storage") });
  const [low, setLow] = useState(part.low_stock_report_quantity?.toString() ?? "");
  const [attrPct, setAttrPct] = useState(String(part.attrition_percentage));
  const [attrMin, setAttrMin] = useState(String(part.attrition_min_quantity));
  const [defStorage, setDefStorage] = useState(part.default_storage_location_id ?? "");
  const [mandatory, setMandatory] = useState(part.default_storage_mandatory);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    setErr(null);
    setBusy(true);
    try {
      await api.patch(`/parts/${partId}`, {
        low_stock_report_quantity: low ? Number(low) : null,
        attrition_percentage: Number(attrPct),
        attrition_min_quantity: Number(attrMin),
        default_storage_location_id: defStorage || null,
        default_storage_mandatory: mandatory,
      });
      qc.invalidateQueries({ queryKey: ["part", partId] });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card p-4 max-w-2xl space-y-3">
      <h3 className="text-md font-semibold">Part settings</h3>
      {err && <div className="text-danger text-sm">{err}</div>}
      <div>
        <label className="label">Low-stock report quantity</label>
        <input className="input" type="number" value={low} onChange={e => setLow(e.target.value)} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">Attrition %</label>
          <input className="input" type="number" step="0.1" value={attrPct} onChange={e => setAttrPct(e.target.value)} />
        </div>
        <div>
          <label className="label">Min attrition qty</label>
          <input className="input" type="number" value={attrMin} onChange={e => setAttrMin(e.target.value)} />
        </div>
      </div>
      <div>
        <label className="label">Default storage location</label>
        <select className="input" value={defStorage} onChange={e => setDefStorage(e.target.value)}>
          <option value="">— none —</option>
          {storage?.filter(s => !s.archived_at).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={mandatory} onChange={e => setMandatory(e.target.checked)} />
        Default location is mandatory
      </label>
      <button className="btn-primary" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</button>
    </div>
  );
}
