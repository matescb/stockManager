import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type Ws = {
  id: string;
  name: string;
  kind: string;
  currency_default: string;
  lot_control_enabled: boolean;
  serial_tracking_enabled: boolean;
};

export default function WorkspaceSettings() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const { data: cur } = useQuery({ queryKey: ["ws", "current"], queryFn: () => api.get<Ws>("/workspaces/current") });
  const [newName, setNewName] = useState("");
  const [err, setErr] = useState<string | null>(null);

  async function createWs() {
    if (!newName.trim()) return;
    await api.post("/workspaces", { name: newName.trim() });
    setNewName("");
    qc.invalidateQueries();
    window.location.reload();
  }

  async function patch(body: Partial<Ws>) {
    setErr(null);
    try {
      await api.patch("/workspaces/current", body);
      qc.invalidateQueries({ queryKey: ["ws", "current"] });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Failed");
    }
  }

  return (
    <div className="max-w-2xl">
      <h1 className="text-xl font-semibold mb-4">Workspace</h1>
      {err && <div className="card p-3 text-danger text-sm mb-3">{err}</div>}
      {cur && (
        <div className="card p-4 mb-4 space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Name</label>
              <input
                className="input"
                defaultValue={cur.name}
                onBlur={e => e.target.value && e.target.value !== cur.name && patch({ name: e.target.value })}
              />
            </div>
            <div>
              <label className="label">Default currency</label>
              <input
                className="input"
                maxLength={3}
                defaultValue={cur.currency_default}
                onBlur={e => {
                  const v = e.target.value.toUpperCase();
                  if (v && v.length === 3 && v !== cur.currency_default) patch({ currency_default: v });
                }}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={cur.lot_control_enabled}
                onChange={e => patch({ lot_control_enabled: e.target.checked })}
              />
              Lot control
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={cur.serial_tracking_enabled}
                onChange={e => patch({ serial_tracking_enabled: e.target.checked })}
              />
              Serial tracking
            </label>
          </div>
          {cur.serial_tracking_enabled && (
            <div className="text-xs text-muted">
              When on, parts marked <em>serialized</em> require <code>lot.serial_number</code>
              {" "}and quantity 1 per add-stock or receive line.
            </div>
          )}
          <div><span className="text-muted">Kind:</span> {cur.kind}</div>
        </div>
      )}
      <h2 className="text-md font-semibold mb-2">All workspaces</h2>
      <ul className="space-y-1 mb-4">
        {me?.workspaces.map(w => (
          <li key={w.id} className="text-sm card p-3 flex items-center justify-between">
            <span>{w.name} <span className="pill ml-2">{w.kind}</span></span>
            <span className="font-mono text-xs text-muted">{w.id}</span>
          </li>
        ))}
      </ul>
      <h2 className="text-md font-semibold mb-2">Create new workspace</h2>
      <div className="flex gap-2">
        <input className="input max-w-xs" placeholder="Workspace name" value={newName} onChange={e => setNewName(e.target.value)} />
        <button className="btn-primary" onClick={createWs}>Create</button>
      </div>
    </div>
  );
}
