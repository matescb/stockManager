/**
 * Multi-stage builds (Track B2) — the stages panel on the build detail page.
 *
 * A build with no stages is a single-pass build and keeps the whole-BOM
 * "Consumption plan" card below; adding a stage switches the build to
 * per-stage consumption (the whole-build endpoint refuses staged builds,
 * so the two can never double-consume the same BOM).
 *
 * Reservations are taken once, up front, when the build is created —
 * creating a stage writes no ledger row, and each stage consume releases
 * only its own slice. See `docs/domain/builds-and-bom.md`.
 */
import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { stockReportKeys, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import type { Build, BuildStage, Part, ProjectEntry, StorageLocation } from "@/types";

type StageLineDraft = { project_entry_id: string; portion_pct: number };

type CreateStagePayload = {
  name: string;
  lines: StageLineDraft[];
};

type StageConsumePayload = {
  lines: { project_entry_id: string; part_id: string; quantity: number; storage_location_id?: string }[];
  output_lot_name?: string;
  output_storage_location_id?: string;
};

type Props = {
  buildId: string;
  build: Build;
  /** Active stages in consumption order; empty for a single-pass build. */
  stages: BuildStage[];
  entries: ProjectEntry[] | undefined;
  partsById: Map<string, Part>;
  storage: StorageLocation[] | undefined;
  isEditable: boolean;
};

const STATUS_TONE: Record<string, string> = {
  complete: "bg-success/20 text-success",
  in_progress: "bg-warning/20 text-warning",
};

export default function BuildStagesPanel({
  buildId,
  build,
  stages,
  entries,
  partsById,
  storage,
  isEditable,
}: Props) {
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const [showForm, setShowForm] = useState(false);
  const [stageName, setStageName] = useState("");
  const [draftLines, setDraftLines] = useState<Record<string, number>>({});
  const [consumeStorage, setConsumeStorage] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);

  const consumableEntries = useMemo(
    () =>
      (entries ?? []).filter(
        e => !e.dnp && e.part_id && (e.entry_type === "part" || e.entry_type === "meta_part"),
      ),
    [entries],
  );

  function invalidateAll() {
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "build", buildId) });
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "builds") });
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "parts") });
    for (const queryKey of stockReportKeys(workspaceId)) {
      qc.invalidateQueries({ queryKey });
    }
  }

  const createMutation = useApiMutation<unknown, CreateStagePayload>({
    mutationKey: ["build", buildId, "create-stage"],
    mutationFn: payload => api.post(`/builds/${buildId}/stages`, payload),
    onSuccess: () => {
      invalidateAll();
      setShowForm(false);
      setStageName("");
      setDraftLines({});
      setErr(null);
      toast.success("Stage added.");
    },
    onError: e => {
      const m = e instanceof ApiError ? e.userMessage : "Could not add stage";
      setErr(m);
      toast.error(m);
    },
  });

  const consumeMutation = useApiMutation<unknown, { stageId: string; body: StageConsumePayload }>({
    mutationKey: ["build", buildId, "consume-stage"],
    mutationFn: ({ stageId, body }) => api.post(`/builds/${buildId}/stages/${stageId}/consume`, body),
    onSuccess: () => {
      invalidateAll();
      setErr(null);
      toast.success("Stage consumed — stock decremented.");
    },
    onError: e => {
      const m = e instanceof ApiError ? e.userMessage : "Stage consume failed";
      setErr(m);
      toast.error(m);
    },
  });

  function toggleLine(entryId: string, checked: boolean) {
    setDraftLines(prev => {
      if (!checked) {
        const { [entryId]: _dropped, ...rest } = prev;
        return rest;
      }
      return { ...prev, [entryId]: prev[entryId] ?? 100 };
    });
  }

  function submitStage() {
    setErr(null);
    const lines = Object.entries(draftLines).map(([project_entry_id, portion_pct]) => ({
      project_entry_id,
      portion_pct,
    }));
    if (!stageName.trim()) {
      setErr("Stage needs a name.");
      return;
    }
    if (lines.length === 0) {
      setErr("Pick at least one BOM line for the stage.");
      return;
    }
    createMutation.mutate({ name: stageName.trim(), lines });
  }

  function consume(stage: BuildStage) {
    setErr(null);
    const storageId = consumeStorage[stage.id] || undefined;
    const lines = stage.shortage
      .filter(row => row.required > 0)
      .map(row => ({
        project_entry_id: row.project_entry_id,
        part_id: row.part_id,
        quantity: row.required,
        storage_location_id: storageId,
      }));
    if (lines.length === 0) {
      setErr("Stage has nothing to consume.");
      return;
    }
    consumeMutation.mutate({ stageId: stage.id, body: { lines } });
  }

  // The next stage waiting to be consumed. Stages are consumed in sequence,
  // so only one of them can act at a time.
  const nextStageId = stages.find(s => s.status !== "complete")?.id ?? null;
  const busy = consumeMutation.isPending || createMutation.isPending;

  if (stages.length === 0 && !isEditable) return null;

  return (
    <div className="card p-4 mb-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-md font-semibold">Assembly stages</h3>
        {isEditable && !build.archived_at && (
          <button className="btn text-xs" onClick={() => setShowForm(v => !v)}>
            {showForm ? "Cancel" : "+ Add stage"}
          </button>
        )}
      </div>

      {err && <div className="text-danger text-sm mb-3">{err}</div>}

      {stages.length === 0 ? (
        <div className="text-muted text-sm">
          No stages — this build is consumed in a single pass. Add a stage to assemble the
          BOM progressively; stock is then drawn down one stage at a time.
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {stages.map(stage => (
            <div key={stage.id} className="border border-border rounded p-3">
              <div className="flex items-center justify-between mb-2">
                <div className="font-medium">
                  <span className="text-muted text-xs mr-2">#{stage.sequence + 1}</span>
                  {stage.name}
                  <span className={`pill ml-2 ${STATUS_TONE[stage.status] ?? ""}`}>{stage.status}</span>
                </div>
                {isEditable && !build.archived_at && stage.id === nextStageId && (
                  <div className="flex items-center gap-2">
                    <select
                      className="input"
                      aria-label={`Storage for ${stage.name}`}
                      value={consumeStorage[stage.id] ?? ""}
                      onChange={ev =>
                        setConsumeStorage(prev => ({ ...prev, [stage.id]: ev.target.value }))
                      }
                    >
                      <option value="">— any storage —</option>
                      {storage?.filter(s => !s.archived_at).map(s => (
                        <option key={s.id} value={s.id}>{s.name}</option>
                      ))}
                    </select>
                    <button className="btn-primary text-xs" disabled={busy} onClick={() => consume(stage)}>
                      {consumeMutation.isPending ? "Consuming…" : "Consume stage"}
                    </button>
                  </div>
                )}
              </div>
              {stage.shortage.length === 0 ? (
                <div className="text-muted text-sm">No consumable lines in this stage.</div>
              ) : (
                <table className="table">
                  <thead>
                    <tr>
                      <th>Part</th>
                      <th>Portion</th>
                      <th>Required</th>
                      <th>On hand</th>
                      <th>Short</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stage.shortage.map(row => (
                      <tr key={row.project_entry_id}>
                        <td>{row.part_name}</td>
                        <td className="tabular-nums text-muted">{row.portion_pct}%</td>
                        {/* Already attrition-adjusted: this is a slice of `_required`. */}
                        <td className="tabular-nums">{row.required}</td>
                        <td className="tabular-nums">{row.available}</td>
                        <td className={`tabular-nums ${row.short_by ? "text-danger" : ""}`}>
                          {row.short_by || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <div className="mt-4 border-t border-border pt-3">
          <div className="mb-3">
            <label className="label" htmlFor="stage-name">Stage name</label>
            <input
              id="stage-name"
              className="input"
              value={stageName}
              onChange={e => setStageName(e.target.value)}
              placeholder="SMT reflow"
            />
          </div>
          <div className="text-sm text-muted mb-2">
            Pick the BOM lines this stage consumes. The portion is a percentage of each
            line&apos;s whole-build requirement, so attrition still applies; portions across
            all stages of one line may not exceed 100%.
          </div>
          <table className="table">
            <thead>
              <tr>
                <th className="w-10"></th>
                <th>BOM line</th>
                <th className="w-28">Portion %</th>
              </tr>
            </thead>
            <tbody>
              {consumableEntries.map(e => {
                const checked = e.id in draftLines;
                return (
                  <tr key={e.id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Include ${partsById.get(e.part_id!)?.name ?? e.part_id}`}
                        checked={checked}
                        onChange={ev => toggleLine(e.id, ev.target.checked)}
                      />
                    </td>
                    <td>{partsById.get(e.part_id!)?.name ?? e.part_id}</td>
                    <td>
                      <input
                        className="input"
                        type="number"
                        min={0}
                        max={100}
                        step="0.01"
                        disabled={!checked}
                        value={checked ? draftLines[e.id] : ""}
                        onChange={ev =>
                          setDraftLines(prev => ({ ...prev, [e.id]: Number(ev.target.value) }))
                        }
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="mt-3 flex justify-end">
            <button className="btn-primary" disabled={busy} onClick={submitStage}>
              {createMutation.isPending ? "Adding…" : "Add stage"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
