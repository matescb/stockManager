import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import EntityHeader from "@/components/EntityHeader";
import type { Build, BuildShortageRow, Part, Project, ProjectEntry, StorageLocation } from "@/types";

type DetailOut = { build: Build; shortage: BuildShortageRow[] };

type ConsumeRow = { part_id: string; quantity: number; storage_location_id?: string };

export default function BuildDetail() {
  const { buildId } = useParams<{ buildId: string }>();
  const qc = useQueryClient();
  const nav = useNavigate();

  const { data } = useQuery({
    queryKey: ["build", buildId],
    queryFn: () => api.get<DetailOut>(`/builds/${buildId}`),
    enabled: !!buildId,
  });
  const projectId = data?.build.project_id;
  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.get<Project>(`/projects/${projectId}`),
    enabled: !!projectId,
  });
  const { data: entries } = useQuery({
    queryKey: ["project", projectId, "entries"],
    queryFn: () => api.get<ProjectEntry[]>(`/projects/${projectId}/entries`),
    enabled: !!projectId,
  });
  const { data: parts } = useQuery({ queryKey: ["parts"], queryFn: () => api.get<Part[]>("/parts") });
  const { data: storage } = useQuery({ queryKey: ["storage"], queryFn: () => api.get<StorageLocation[]>("/storage") });

  // consumption plan: project_entry_id → list of consume rows
  const [plan, setPlan] = useState<Record<string, ConsumeRow[]>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const partsById = useMemo(() => new Map(parts?.map(p => [p.id, p]) ?? []), [parts]);

  if (!data) return <div className="text-muted">Loading…</div>;
  const { build, shortage } = data;
  const isEditable = build.status === "planned" || build.status === "in_progress";
  const shortageByEntry = new Map(shortage.map(s => [s.project_entry_id, s]));

  function suggestedFill(s: BuildShortageRow): ConsumeRow[] {
    if (s.required <= s.available) {
      return [{ part_id: s.part_id, quantity: s.required }];
    }
    const main = { part_id: s.part_id, quantity: s.available };
    const remaining = s.required - s.available;
    return remaining > 0 && s.substitute_ids.length
      ? [main, { part_id: s.substitute_ids[0], quantity: remaining }]
      : [main];
  }

  function fillSuggested() {
    const next: Record<string, ConsumeRow[]> = {};
    for (const s of shortage) next[s.project_entry_id] = suggestedFill(s);
    setPlan(next);
  }

  async function doConsume() {
    setErr(null);
    setBusy(true);
    try {
      const lines = Object.entries(plan).flatMap(([entryId, rows]) =>
        rows.filter(r => r.quantity > 0).map(r => ({
          project_entry_id: entryId,
          part_id: r.part_id,
          quantity: r.quantity,
          storage_location_id: r.storage_location_id || undefined,
        })),
      );
      if (lines.length === 0) {
        setErr("No lines.");
        return;
      }
      await api.post(`/builds/${buildId}/consume`, { lines });
      qc.invalidateQueries({ queryKey: ["build", buildId] });
      qc.invalidateQueries({ queryKey: ["parts"] });
      toast.success("Build complete — stock decremented.");
    } catch (e) {
      const m = e instanceof ApiError ? e.message : "Build failed";
      setErr(m);
      toast.error(m);
    } finally {
      setBusy(false);
    }
  }

  async function doArchive() {
    await api.post(`/builds/${buildId}/${build.archived_at ? "restore" : "archive"}`);
    qc.invalidateQueries({ queryKey: ["build", buildId] });
    qc.invalidateQueries({ queryKey: ["builds"] });
    if (!build.archived_at) nav("/builds");
  }

  function setRow(entryId: string, rowIdx: number, patch: Partial<ConsumeRow>) {
    setPlan(p => {
      const cur = p[entryId] ?? [];
      const next = cur.map((r, i) => i === rowIdx ? { ...r, ...patch } : r);
      return { ...p, [entryId]: next };
    });
  }
  function addRow(entryId: string, defaultPart: string) {
    setPlan(p => ({ ...p, [entryId]: [...(p[entryId] ?? []), { part_id: defaultPart, quantity: 0 }] }));
  }

  return (
    <div>
      <EntityHeader
        title={build.name}
        subtitle={
          <span>
            {project?.name && <span className="mr-2">{project.name}</span>}
            <span className="pill">{build.status}</span>
            <span className="ml-2 text-muted">qty {build.quantity}</span>
          </span>
        }
        actions={
          <div className="flex gap-2">
            {isEditable && (
              <button className="btn" onClick={fillSuggested}>Auto-fill</button>
            )}
            <button className="btn" onClick={doArchive}>{build.archived_at ? "Restore" : "Archive"}</button>
          </div>
        }
      />

      {err && <div className="card p-3 text-danger text-sm mb-3">{err}</div>}

      <div className="card p-4 mb-4">
        <h3 className="text-md font-semibold mb-3">Shortage analysis</h3>
        {shortage.length === 0 ? (
          <div className="text-muted text-sm">Project has no consumable BOM lines.</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Part</th>
                <th>Required</th>
                <th>On hand</th>
                <th>Substitutes (Σ)</th>
                <th>Short</th>
              </tr>
            </thead>
            <tbody>
              {shortage.map(s => (
                <tr key={s.project_entry_id}>
                  <td>{s.part_name}</td>
                  <td className="tabular-nums">{s.required}</td>
                  <td className="tabular-nums">{s.available}</td>
                  <td className="tabular-nums text-muted">{s.substitute_available}</td>
                  <td className={`tabular-nums ${s.short_by ? "text-danger" : ""}`}>{s.short_by || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {isEditable && entries && (
        <div className="card p-4 mb-4">
          <h3 className="text-md font-semibold mb-3">Consumption plan</h3>
          <div className="text-sm text-muted mb-2">
            One line per consumed part. Multiple lines per entry are allowed (e.g. main part + substitute).
            Use <strong>Auto-fill</strong> for a default plan, then tweak.
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Entry / Required</th>
                <th>Part used</th>
                <th>Storage</th>
                <th className="w-20">Qty</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {entries.filter(e => !e.dnp && e.part_id && (e.entry_type === "part" || e.entry_type === "meta_part")).map(e => {
                const s = shortageByEntry.get(e.id);
                const rows = plan[e.id] ?? [];
                if (rows.length === 0) {
                  // ensure at least one row for the main part
                  return (
                    <tr key={e.id}>
                      <td>
                        <div className="font-medium">{partsById.get(e.part_id!)?.name ?? e.part_id}</div>
                        <div className="text-xs text-muted">need {s?.required ?? "—"}</div>
                      </td>
                      <td colSpan={4}>
                        <button className="btn text-xs" onClick={() => addRow(e.id, e.part_id!)}>+ Add line</button>
                      </td>
                    </tr>
                  );
                }
                return rows.map((row, idx) => (
                  <tr key={`${e.id}:${idx}`}>
                    {idx === 0 ? (
                      <td rowSpan={rows.length}>
                        <div className="font-medium">{partsById.get(e.part_id!)?.name ?? e.part_id}</div>
                        <div className="text-xs text-muted">need {s?.required ?? "—"}</div>
                      </td>
                    ) : null}
                    <td>
                      <select
                        className="input"
                        value={row.part_id}
                        onChange={ev => setRow(e.id, idx, { part_id: ev.target.value })}
                      >
                        <option value={e.part_id!}>{partsById.get(e.part_id!)?.name ?? "main"}</option>
                        {s?.substitute_ids.map(sid => (
                          <option key={sid} value={sid}>{partsById.get(sid)?.name ?? sid}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        className="input"
                        value={row.storage_location_id ?? ""}
                        onChange={ev => setRow(e.id, idx, { storage_location_id: ev.target.value || undefined })}
                      >
                        <option value="">— any —</option>
                        {storage?.filter(s => !s.archived_at).map(s => (
                          <option key={s.id} value={s.id}>{s.name}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        className="input"
                        type="number"
                        min={0}
                        value={row.quantity || ""}
                        onChange={ev => setRow(e.id, idx, { quantity: Number(ev.target.value) })}
                      />
                    </td>
                    <td>
                      {idx === rows.length - 1 && (
                        <button className="btn text-xs" onClick={() => addRow(e.id, e.part_id!)}>+ Sub</button>
                      )}
                    </td>
                  </tr>
                ));
              })}
            </tbody>
          </table>
          <div className="mt-3 flex justify-end">
            <button className="btn-primary" onClick={doConsume} disabled={busy}>
              {busy ? "Consuming…" : "Consume & complete build"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
