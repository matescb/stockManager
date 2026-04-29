import { useState } from "react";
import { useOutletContext } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, Trash2 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { Part } from "@/types";

type CustomField = { id: string; key: string; value: string | null };

// Keys reserved for non-spec metadata (rendered elsewhere on PartInfo).
// We hide them from the Specs table to keep it focused on actual specs.
const RESERVED = new Set(["image_url", "datasheet_url"]);

/**
 * The "Specs" tab on a part. Renders all custom_fields entries on the
 * part as a key/value table, with inline add + delete. Provider-supplied
 * rows (e.g. Mouser ProductAttributes) are persisted here at part-create
 * time; users can extend the table with their own rows afterwards.
 */
export default function PartSpecs() {
  const { part } = useOutletContext<{ part: Part }>();
  const qc = useQueryClient();
  const queryKey = ["part", part.id, "custom-fields"];

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () =>
      api.get<CustomField[]>(`/custom-fields/by-object/part/${part.id}`),
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

  async function update(row: CustomField, newValueText: string) {
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

  async function remove(row: CustomField) {
    if (!confirm(`Delete spec "${row.key}"?`)) return;
    try {
      await api.delete(`/custom-fields/${row.id}`);
      qc.invalidateQueries({ queryKey });
      toast.success("Spec deleted.");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Failed");
    }
  }

  const rows = (data ?? []).filter(r => !RESERVED.has(r.key));

  return (
    <div className="card p-4 max-w-3xl">
      <div className="flex items-center mb-3">
        <h3 className="text-md font-semibold">Specifications</h3>
        <span className="ml-2 text-xs text-muted">{rows.length} {rows.length === 1 ? "row" : "rows"}</span>
      </div>

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
              <th className="w-12"></th>
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
                </td>
                <td>
                  <button
                    type="button"
                    className="btn-ghost btn-sm"
                    aria-label={`Delete ${r.key}`}
                    onClick={() => remove(r)}
                  >
                    <Trash2 size={14} className="text-danger" />
                  </button>
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
