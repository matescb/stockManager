import { useState } from "react";
import { useOutletContext } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, RotateCcw, Trash2 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { CustomFieldRow, Part, SpecSource } from "@/types";

// Reserved keys that surface elsewhere on PartInfo (Media card). We hide
// them from the Specs table to keep it focused on actual specs.
const RESERVED = new Set(["image_url", "datasheet_url"]);

const SOURCE_BADGE: Record<SpecSource, string> = {
  provider: "bg-accent/15 text-accent",
  manual:   "bg-panel2 text-muted",
  override: "bg-warning/20 text-warning",
};

const SOURCE_LABEL: Record<SpecSource, string> = {
  provider: "Provider",
  manual:   "Manual",
  override: "Override",
};

const PROVIDER_LABEL: Record<string, string> = {
  mouser: "Mouser",
  digikey: "DigiKey",
};

// Per-provider deep link to the catalog search for an MPN. Used as a
// fallback when we don't have a stored canonical product URL.
const PROVIDER_SEARCH_URL: Record<string, (mpn: string) => string> = {
  mouser: mpn => `https://www.mouser.com/c/?q=${encodeURIComponent(mpn)}`,
  digikey: mpn => `https://www.digikey.com/en/products/result?keywords=${encodeURIComponent(mpn)}`,
};

/**
 * The "Specs" tab on a part. Provider-supplied rows (from the
 * configured MPN provider) carry source='provider'; user-typed rows
 * are source='manual'; editing a provider row in place transitions it
 * to source='override' and preserves the upstream value as
 * original_value, so a single click on Restore reverts cleanly.
 */
export default function PartSpecs() {
  const { part } = useOutletContext<{ part: Part }>();
  const qc = useQueryClient();
  const queryKey = ["part", part.id, "custom-fields"];

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () =>
      api.get<CustomFieldRow[]>(`/custom-fields/by-object/part/${part.id}`),
  });

  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [busy, setBusy] = useState(false);

  async function add() {
    const k = newKey.trim();
    const v = newValue.trim();
    if (!k) {
      toast.error("Key is required.");
      return;
    }
    setBusy(true);
    try {
      await api.post("/custom-fields", {
        object_type: "part",
        object_id: part.id,
        key: k,
        value: v,
      });
      setNewKey("");
      setNewValue("");
      qc.invalidateQueries({ queryKey });
      toast.success("Spec added.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  async function update(row: CustomFieldRow, newValueText: string) {
    if ((row.value ?? "") === newValueText) return;
    try {
      await api.post("/custom-fields", {
        object_type: "part",
        object_id: part.id,
        key: row.key,
        value: newValueText,
      });
      qc.invalidateQueries({ queryKey });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed");
    }
  }

  async function remove(row: CustomFieldRow) {
    if (!confirm(`Delete spec "${row.key}"?`)) return;
    try {
      await api.delete(`/custom-fields/${row.id}`);
      qc.invalidateQueries({ queryKey });
      toast.success("Spec deleted.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed");
    }
  }

  async function restore(row: CustomFieldRow) {
    try {
      await api.delete(`/custom-fields/${row.id}/override`);
      qc.invalidateQueries({ queryKey });
      toast.success("Restored upstream value.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed");
    }
  }

  const rows = (data ?? []).filter(r => !RESERVED.has(r.key));
  const providerCount = rows.filter(r => r.source === "provider").length;
  const showSparseHint =
    !!part.linked_provider && providerCount > 0 && providerCount < 4;

  return (
    <div className="card p-4 max-w-3xl">
      <div className="flex items-center mb-3">
        <h3 className="text-md font-semibold">Specifications</h3>
        <span className="ml-2 text-xs text-muted">{rows.length} {rows.length === 1 ? "row" : "rows"}</span>
      </div>

      {showSparseHint && (
        <div className="rounded-md border border-border bg-panel2/50 p-3 mb-3 text-xs text-muted">
          {PROVIDER_LABEL[part.linked_provider!] ?? part.linked_provider}'s
          API doesn't always expose the full parametric table — what's
          shown above is what we could pull. Add specs below, or copy
          remaining ones from the
          {" "}
          <a
            className="text-accent hover:underline"
            href={(PROVIDER_SEARCH_URL[part.linked_provider!] ?? PROVIDER_SEARCH_URL.mouser)(part.mpn ?? "")}
            target="_blank"
            rel="noreferrer"
          >
            product page
          </a>.
        </div>
      )}

      {isLoading ? (
        <div className="text-muted text-sm">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="text-sm text-muted py-4">
          No specs yet. Add one below, or create a linked-type part with an
          MPN to auto-populate from the configured provider.
        </div>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th className="w-1/3">Key</th>
              <th>Value</th>
              <th className="w-32">Source</th>
              <th className="w-20"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id}>
                <td className="font-medium">{r.key}</td>
                <td>
                  <input
                    className="input"
                    defaultValue={r.value ?? ""}
                    onBlur={e => update(r, e.target.value)}
                  />
                  {r.source === "override" && r.original_value != null && (
                    <div className="text-xs text-muted mt-1">
                      Upstream: <span className="font-mono">{r.original_value}</span>
                    </div>
                  )}
                </td>
                <td>
                  <span className={`pill ${SOURCE_BADGE[r.source]}`}>
                    {SOURCE_LABEL[r.source]}
                  </span>
                </td>
                <td>
                  <div className="flex gap-1 justify-end">
                    {r.source === "override" && (
                      <button
                        type="button"
                        className="btn-ghost btn-sm"
                        title="Restore upstream value"
                        aria-label={`Restore ${r.key}`}
                        onClick={() => restore(r)}
                      >
                        <RotateCcw size={14} />
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      aria-label={`Delete ${r.key}`}
                      onClick={() => remove(r)}
                    >
                      <Trash2 size={14} className="text-danger" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="mt-4 pt-3 border-t border-border">
        <div className="text-xs uppercase tracking-wider text-muted mb-2">Add a spec</div>
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="Key (e.g. Resistance)"
            value={newKey}
            onChange={e => setNewKey(e.target.value)}
          />
          <input
            className="input flex-1"
            placeholder="Value (e.g. 10 kOhms ±1%)"
            value={newValue}
            onChange={e => setNewValue(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") add(); }}
          />
          <button
            type="button"
            className="btn-primary"
            disabled={busy || !newKey.trim()}
            onClick={add}
          >
            <Plus size={14} /> Add
          </button>
        </div>
      </div>
    </div>
  );
}
