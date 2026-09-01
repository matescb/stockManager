import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { z } from "zod";
import { api, ApiError, getConflictDetail } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { useQuery } from "@tanstack/react-query";
import { useWsKey } from "@/lib/queryKeys";
import { isSafeHttpOrSameOriginUrl } from "@/lib/url";
import { PartCategoriesListSchema, PartCreateSchema, PartSchema } from "@/lib/schemas";
import type { MpnLookupResult, Part, ProviderSpec, StorageLocation } from "@/types";
import MpnLookup from "@/components/MpnLookup";
import { InlineQueryError } from "@/components/QueryStateBoundary";

type PartCreateRequest = z.input<typeof PartCreateSchema>;

function zodMessage(e: z.ZodError): string {
  const issue = e.issues[0];
  const field = issue?.path.join(".");
  return field ? `${field}: ${issue.message}` : issue?.message ?? "Invalid form data.";
}

function mutationMessage(e: unknown): string {
  if (e instanceof ApiError) return e.userMessage;
  if (e instanceof z.ZodError) return zodMessage(e);
  return "Failed";
}

export default function PartCreate() {
  const nav = useNavigate();
  const [form, setForm] = useState({
    // Linked is the typical case for an inventory app fed by Mouser /
    // DigiKey lookups — start there. Operators creating sub-assemblies
    // or local-only parts flip the dropdown.
    part_type: "linked" as "linked" | "local" | "meta" | "sub_assembly",
    name: "",
    manufacturer: "",
    mpn: "",
    internal_part_number: "",
    description: "",
    footprint: "",
    default_storage_location_id: "",
    category_id: "",
    serialized: false,
  });
  const [err, setErr] = useState<string | null>(null);
  // When the create attempt collides on MPN, the 409 carries the existing
  // part's id + name; we surface a link straight to it instead of forcing
  // the operator to manually search.
  const [conflict, setConflict] = useState<{ id: string; name: string } | null>(null);
  // The lookup preview lets the user see what will be loaded from the
  // provider before clicking Create. After the part exists, we run a
  // single refresh-from-provider call which writes all the provider
  // data with the right `source='provider'` tagging — instead of
  // posting each row from the browser as a manual edit.
  const [datasheetUrl, setDatasheetUrl] = useState<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [specs, setSpecs] = useState<ProviderSpec[]>([]);
  const [hasLookup, setHasLookup] = useState(false);
  // When refresh-from-provider fails after a successful create, we surface
  // an inline banner instead of silently swallowing the error. The part
  // is valid — just missing provider-side fields — so we never DELETE it.
  const [refreshFailed, setRefreshFailed] = useState<{ partId: string } | null>(null);
  const storageQuery = useQuery({ queryKey: useWsKey("storage"), queryFn: ({ signal }) => api.get<StorageLocation[]>("/storage", { signal }) });
  const { data: storage } = storageQuery;
  // Active categories only — archived ones aren't selectable.
  const categoriesQuery = useQuery({
    queryKey: useWsKey("categories", { archived: false }),
    queryFn: ({ signal }) => api.parsed.get("/categories", PartCategoriesListSchema, { signal }),
  });
  const { data: categories } = categoriesQuery;
  const safeDatasheetUrl = isSafeHttpOrSameOriginUrl(datasheetUrl) ? datasheetUrl : null;
  const safeImageUrl = isSafeHttpOrSameOriginUrl(imageUrl) ? imageUrl : null;

  // FE2-006: gate concurrent submits via mutationKey so a double-click
  // on Create can't post two parts; the 409 conflict-link branch stays
  // in `onError`, just routed through `getConflictDetail(error)`.
  const createMutation = useApiMutation<Part, PartCreateRequest>({
    mutationKey: ["parts", "create"],
    mutationFn: (payload) =>
      api.parsed.post("/parts", PartSchema, PartCreateSchema.parse(payload)),
    onSuccess: async (res) => {
      // If the user successfully ran the MPN lookup and the part is
      // linked-type with an MPN, re-run the lookup against the new
      // part to populate provider data with proper source='provider'
      // tagging + last_refresh_at + linked_provider.
      if (hasLookup && form.part_type === "linked" && form.mpn.trim()) {
        try {
          await api.post(`/parts/${res.id}/refresh-from-provider`);
        } catch {
          // The part was created successfully — don't navigate away.
          // Surface a banner so the user can retry or open the part anyway.
          setRefreshFailed({ partId: res.id });
          return;
        }
      }
      nav(`/parts/${res.id}/info`);
    },
    onError: (e) => {
      const detail = getConflictDetail(e);
      if (detail) {
        setConflict({ id: detail.existing_id, name: detail.existing_name });
        // Reset stale lookup preview so the next MPN attempt starts clean.
        setHasLookup(false);
        setSpecs([]);
        setImageUrl(null);
        setDatasheetUrl(null);
        setErr(null);
        return;
      }
      setErr(mutationMessage(e));
    },
  });

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm(f => ({ ...f, [k]: v }));
  }

  function applyLookup(r: NonNullable<MpnLookupResult["result"]>) {
    setForm(f => ({
      ...f,
      manufacturer: r.manufacturer ?? f.manufacturer,
      description: r.description ?? f.description,
      footprint: r.footprint ?? f.footprint,
    }));
    setDatasheetUrl(r.datasheet_url ?? null);
    setImageUrl(r.image_url ?? null);
    setSpecs(r.specs ?? []);
    setHasLookup(true);
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setConflict(null);
    setRefreshFailed(null);
    const payload: PartCreateRequest = {
      part_type: form.part_type,
      manufacturer: form.manufacturer,
      mpn: form.mpn,
      internal_part_number: form.internal_part_number,
      description: form.description,
      footprint: form.footprint,
      serialized: form.serialized,
    };
    if (form.default_storage_location_id) {
      payload.default_storage_location_id = form.default_storage_location_id;
    }
    if (form.category_id) {
      payload.category_id = form.category_id;
    }
    // Send blank name as undefined so the server defaults it to mpn.
    if (form.name?.trim()) payload.name = form.name;
    createMutation.mutate(payload);
  }

  const busy = createMutation.isPending;

  async function retryRefresh() {
    if (!refreshFailed) return;
    try {
      await api.post(`/parts/${refreshFailed.partId}/refresh-from-provider`);
      setRefreshFailed(null);
      nav(`/parts/${refreshFailed.partId}/info`);
    } catch {
      // Keep the banner visible — user can try again or open anyway.
    }
  }

  return (
    <form onSubmit={submit} className="max-w-2xl card p-4 space-y-3">
      <h1 className="text-xl font-semibold">Create part</h1>
      {err && <div className="text-danger text-sm">{err}</div>}
      {refreshFailed && (
        <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm space-y-2">
          <p>Provider data couldn&apos;t be fetched. The part was created — retry refresh or open it and try again later.</p>
          <div className="flex gap-2">
            <button type="button" className="btn-primary" onClick={retryRefresh}>
              Retry refresh
            </button>
            <button type="button" className="btn" onClick={() => nav(`/parts/${refreshFailed.partId}/info`)}>
              Open part anyway
            </button>
          </div>
        </div>
      )}
      {conflict && (
        <div data-testid="mpn-conflict-banner" className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm">
          MPN <span className="font-mono">{form.mpn}</span> is already used
          by part <strong>{conflict.name}</strong>.{" "}
          <Link to={`/parts/${conflict.id}/info`} className="text-accent underline">
            Open existing part →
          </Link>
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="part-create-type">Type</label>
          <select id="part-create-type" className="input" value={form.part_type} onChange={e => set("part_type", e.target.value as "linked" | "local" | "meta" | "sub_assembly")}>
            <option value="linked">Linked (MPN)</option>
            <option value="local">Local</option>
            <option value="meta">Meta-part</option>
            <option value="sub_assembly">Sub-assembly</option>
          </select>
        </div>
        <div>
          <label className="label" htmlFor="part-create-footprint">Footprint</label>
          <input id="part-create-footprint" className="input" value={form.footprint} onChange={e => set("footprint", e.target.value)} placeholder="0402, SOIC-8…" />
        </div>
      </div>
      <div>
        <label className="label" htmlFor="part-create-name">Name</label>
        <input
          id="part-create-name"
          className="input"
          value={form.name}
          onChange={e => set("name", e.target.value)}
          placeholder={form.mpn.trim() ? form.mpn.trim() : "(required if no MPN)"}
        />
        <div className="text-xs text-muted mt-1">
          Defaults to the MPN when left blank.
        </div>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="part-create-manufacturer">Manufacturer</label>
          <input id="part-create-manufacturer" className="input" value={form.manufacturer} onChange={e => set("manufacturer", e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="part-create-mpn">MPN</label>
          <div className="flex items-end gap-2">
            <input id="part-create-mpn" className="input flex-1" value={form.mpn} onChange={e => set("mpn", e.target.value)} />
            {form.part_type === "linked" && <MpnLookup mpn={form.mpn} onResult={applyLookup} />}
          </div>
          {safeDatasheetUrl && (
            <div className="text-xs text-muted mt-1">
              Datasheet: <a className="underline" href={safeDatasheetUrl} target="_blank" rel="noopener noreferrer">{safeDatasheetUrl}</a>
            </div>
          )}
        </div>
      </div>
      {(safeImageUrl || specs.length > 0) && (
        <div className="rounded-md border border-border bg-panel2/50 p-3 space-y-2 text-sm">
          <div className="flex items-start gap-3">
            {safeImageUrl && (
              <img src={safeImageUrl} alt="Part" className="h-16 w-16 object-contain rounded bg-panel" />
            )}
            <div className="flex-1 text-xs text-muted">
              Found via provider lookup. After Create, the part will be linked
              and these specs will be tagged as <strong>Provider</strong>;
              click Refresh on the part page to re-pull whenever you want.
            </div>
          </div>
          {specs.length > 0 && (
            <table className="text-xs w-full">
              <tbody>
                {specs.map(s => (
                  <tr key={s.key} className="align-top">
                    <td className="text-muted pr-3 py-0.5 whitespace-nowrap">{s.key}</td>
                    <td className="py-0.5">{s.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      <div>
        <label className="label" htmlFor="part-create-category">Category</label>
        <InlineQueryError query={categoriesQuery} label="categories" className="mb-2" />
        <select
          id="part-create-category"
          className="input"
          value={form.category_id}
          onChange={e => set("category_id", e.target.value)}
        >
          <option value="">— none —</option>
          {categories?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>
      <div>
        <label className="label" htmlFor="part-create-ipn">Internal part number</label>
        <input id="part-create-ipn" className="input" value={form.internal_part_number} onChange={e => set("internal_part_number", e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="part-create-description">Description</label>
        <textarea id="part-create-description" className="input" rows={3} value={form.description} onChange={e => set("description", e.target.value)} />
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form.serialized} onChange={e => set("serialized", e.target.checked)} />
        Serialized (one unit per lot, requires serial number — only enforced when the workspace has serial tracking on)
      </label>
      <div>
        <label className="label" htmlFor="part-create-default-storage">Default storage location</label>
        <InlineQueryError query={storageQuery} label="storage locations" className="mb-2" />
        <select id="part-create-default-storage" className="input" value={form.default_storage_location_id} onChange={e => set("default_storage_location_id", e.target.value)}>
          <option value="">— none —</option>
          {storage?.filter(s => !s.archived_at).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>
      <div className="flex gap-2">
        <button className="btn-primary" disabled={busy || !!refreshFailed}>{busy ? "Creating…" : "Create"}</button>
        <button type="button" className="btn" onClick={() => nav("/parts")}>Cancel</button>
      </div>
    </form>
  );
}
