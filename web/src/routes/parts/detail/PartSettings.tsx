import { useState } from "react";
import { useOutletContext, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import type { Part, StorageLocation } from "@/types";

type PartPatch = {
  low_stock_report_quantity: number | null;
  attrition_percentage: number;
  attrition_min_quantity: number;
  default_storage_location_id: string | null;
  default_storage_mandatory: boolean;
  serialized: boolean;
  published: boolean;
};

export default function PartSettings() {
  const { part } = useOutletContext<{ part: Part }>();
  const { partId } = useParams();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const { data: storage } = useQuery({ queryKey: useWsKey("storage"), queryFn: () => api.get<StorageLocation[]>("/storage") });
  const [low, setLow] = useState(part.low_stock_report_quantity?.toString() ?? "");
  const [attrPct, setAttrPct] = useState(String(part.attrition_percentage));
  const [attrMin, setAttrMin] = useState(String(part.attrition_min_quantity));
  const [defStorage, setDefStorage] = useState(part.default_storage_location_id ?? "");
  const [mandatory, setMandatory] = useState(part.default_storage_mandatory);
  const [serialized, setSerialized] = useState(part.serialized);
  const [published, setPublished] = useState(!!part.published);
  const [err, setErr] = useState<string | null>(null);

  // FE2-006: a single mutation per part-id keyed on the resource so
  // double-clicks don't fire two PATCHes. The form only saves a single
  // entity so optimistic update isn't worth the cache-shape coupling
  // here — we just invalidate on success.
  const saveMutation = useApiMutation<unknown, PartPatch>({
    mutationKey: ["part", partId, "settings"],
    mutationFn: (payload) => api.patch(`/parts/${partId}`, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", partId) });
    },
    onError: (e) => {
      setErr(e instanceof ApiError ? e.userMessage : "Failed");
    },
  });

  function save() {
    setErr(null);
    saveMutation.mutate({
      low_stock_report_quantity: low ? Number(low) : null,
      attrition_percentage: Number(attrPct),
      attrition_min_quantity: Number(attrMin),
      default_storage_location_id: defStorage || null,
      default_storage_mandatory: mandatory,
      serialized,
      published,
    });
  }

  const busy = saveMutation.isPending;

  return (
    <div className="card p-4 max-w-2xl space-y-3">
      <h3 className="text-md font-semibold">Part settings</h3>
      {err && <div className="text-danger text-sm">{err}</div>}
      <div>
        <label className="label" htmlFor="ps-low-stock">Low-stock report quantity</label>
        <input id="ps-low-stock" className="input" type="number" value={low} onChange={e => setLow(e.target.value)} />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="ps-attrition-pct">Attrition %</label>
          <input id="ps-attrition-pct" className="input" type="number" step="0.1" value={attrPct} onChange={e => setAttrPct(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="ps-attrition-min">Min attrition qty</label>
          <input id="ps-attrition-min" className="input" type="number" value={attrMin} onChange={e => setAttrMin(e.target.value)} />
        </div>
      </div>
      <div>
        <label className="label" htmlFor="ps-default-storage">Default storage location</label>
        <select id="ps-default-storage" className="input" value={defStorage} onChange={e => setDefStorage(e.target.value)}>
          <option value="">— none —</option>
          {storage?.filter(s => !s.archived_at).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={mandatory} onChange={e => setMandatory(e.target.checked)} />
        Default location is mandatory
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={serialized} onChange={e => setSerialized(e.target.checked)} />
        Serialized (one unit per lot — enforced when workspace has serial tracking on)
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={published} onChange={e => setPublished(e.target.checked)} />
        Published (show in the workspace's public catalog when enabled)
      </label>
      <button className="btn-primary" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</button>
    </div>
  );
}
