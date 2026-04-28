import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
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

  async function createWs() {
    if (!newName.trim()) return;
    await api.post("/workspaces", { name: newName.trim() });
    setNewName("");
    qc.invalidateQueries();
    window.location.reload();
  }

  return (
    <div className="max-w-2xl">
      <h1 className="text-xl font-semibold mb-4">Workspace</h1>
      {cur && (
        <div className="card p-4 mb-4 space-y-2 text-sm">
          <div><span className="text-muted">Name:</span> {cur.name}</div>
          <div><span className="text-muted">Kind:</span> {cur.kind}</div>
          <div><span className="text-muted">Currency:</span> {cur.currency_default}</div>
          <div><span className="text-muted">Lot control:</span> {cur.lot_control_enabled ? "on" : "off"}</div>
          <div><span className="text-muted">Serial tracking:</span> {cur.serial_tracking_enabled ? "on" : "off"}</div>
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
