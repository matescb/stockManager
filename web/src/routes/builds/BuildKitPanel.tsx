/**
 * Kitting (Track B3) — the "consolidate this build onto one tray" panel.
 *
 * Pick a staging location, see exactly what would move and from which bin,
 * then commit it in one call. The preview (`GET …/kit-plan`) and the commit
 * (`POST …/kit`) return the same body shape, so one table renders both.
 *
 * Three things the UI has to be honest about, because they are the
 * feature's contract (see `docs/domain/builds-and-bom.md#kitting`):
 *
 *  - a kit **tops the tray up** to what this pass needs, so re-running it
 *    moves nothing — "already there" is a column, not a hidden subtraction;
 *  - partial availability **moves what exists** and reports the shortfall
 *    rather than refusing, so the short column has to be prominent;
 *  - a staged build is kitted stage by stage (the whole-build endpoint
 *    refuses it), which is why the stage picker appears only then.
 */
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { stockReportKeys, useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import { formatQuantity, formatQuantityPhrase } from "@/lib/format";
import type { Build, BuildStage, StorageLocation } from "@/types";

type KitSource = {
  storage_location_id: string | null;
  storage_location_name: string | null;
  lot_id: string | null;
  quantity: number;
};

type KitLine = {
  part_id: string;
  part_name: string;
  project_entry_ids: string[];
  required: number;
  at_staging: number;
  to_move: number;
  moving: number;
  short_by: number;
  sources: KitSource[];
};

type KitPlan = {
  build_id: string;
  build_stage_id: string | null;
  storage_location_id: string;
  storage_location_name: string;
  executed: boolean;
  lines: KitLine[];
  totals: { lines: number; moving: number; short_by: number; short_lines: number };
};

type Props = {
  buildId: string;
  build: Build;
  /** Active stages in consumption order; empty for a single-pass build. */
  stages: BuildStage[];
  storage: StorageLocation[] | undefined;
  isEditable: boolean;
};

export default function BuildKitPanel({ buildId, build, stages, storage, isEditable }: Props) {
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const [stagingId, setStagingId] = useState("");
  const [stageId, setStageId] = useState("");
  const [err, setErr] = useState<string | null>(null);

  // A staged build is kitted per stage. Default to the next stage waiting
  // to be consumed — the one the operator is about to walk the shelves for.
  const nextStageId = useMemo(
    () => stages.find(s => s.status !== "complete")?.id ?? "",
    [stages],
  );
  const activeStageId = stages.length > 0 ? stageId || nextStageId : "";
  const basePath = activeStageId
    ? `/builds/${buildId}/stages/${activeStageId}`
    : `/builds/${buildId}`;

  const canKit = isEditable && !build.archived_at && (stages.length === 0 || !!activeStageId);

  const { data: plan, isFetching } = useQuery({
    queryKey: useWsKey("build", buildId, "kit-plan", activeStageId || "whole", stagingId),
    queryFn: ({ signal }) =>
      api.get<KitPlan>(
        `${basePath}/kit-plan?storage_location_id=${encodeURIComponent(stagingId)}`,
        { signal },
      ),
    enabled: !!stagingId && canKit,
  });

  const kitMutation = useApiMutation<KitPlan, { storage_location_id: string }>({
    mutationKey: ["build", buildId, "kit", activeStageId],
    mutationFn: payload => api.post<KitPlan>(`${basePath}/kit`, payload),
    onSuccess: result => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "build", buildId) });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "parts") });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "storage") });
      for (const queryKey of stockReportKeys(workspaceId)) {
        qc.invalidateQueries({ queryKey });
      }
      setErr(null);
      if (result.totals.short_lines > 0) {
        toast.warning(
          `Kitted ${formatQuantity(result.totals.moving)} to ${result.storage_location_name} — ` +
            `${result.totals.short_lines} line(s) short by ${formatQuantity(result.totals.short_by)}.`,
        );
      } else {
        toast.success(
          `Kitted ${formatQuantity(result.totals.moving)} to ${result.storage_location_name}.`,
        );
      }
    },
    onError: e => {
      const m = e instanceof ApiError ? e.userMessage : "Kitting failed";
      setErr(m);
      toast.error(m);
    },
  });

  const kittable = storage?.filter(s => !s.archived_at && !s.is_full) ?? [];
  const busy = kitMutation.isPending;

  if (!canKit) return null;

  return (
    <div className="card p-4 mb-4">
      <h3 className="text-md font-semibold mb-3">Kitting</h3>
      <div className="text-sm text-muted mb-3">
        Consolidate everything this {activeStageId ? "stage" : "build"} needs into one staging
        location, so the components travel to the bench as a tray instead of a shelf-walk.
        Stock is <strong>moved</strong>, not consumed — totals on hand do not change. Re-running
        a kit tops the location up to what is still needed rather than adding a second trayful.
      </div>

      {err && <div className="text-danger text-sm mb-3">{err}</div>}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
        {stages.length > 0 && (
          <div>
            <label className="label" htmlFor="kit-stage">Stage</label>
            <select
              id="kit-stage"
              className="input"
              value={activeStageId}
              onChange={e => setStageId(e.target.value)}
            >
              {stages.map(s => (
                <option key={s.id} value={s.id}>
                  #{s.sequence + 1} {s.name}
                  {s.status === "complete" ? " (complete)" : ""}
                </option>
              ))}
            </select>
          </div>
        )}
        <div>
          <label className="label" htmlFor="kit-staging">Staging location</label>
          <select
            id="kit-staging"
            className="input"
            value={stagingId}
            onChange={e => setStagingId(e.target.value)}
          >
            <option value="">— pick a tray / bin —</option>
            {kittable.map(s => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        </div>
      </div>

      {!stagingId ? (
        <div className="text-muted text-sm">
          Pick a staging location to preview what would move.
        </div>
      ) : isFetching && !plan ? (
        <div className="text-muted text-sm">Working out the plan…</div>
      ) : !plan ? null : plan.lines.length === 0 ? (
        <div className="text-muted text-sm">Nothing to kit — no consumable BOM lines.</div>
      ) : (
        <>
          <table className="table">
            <thead>
              <tr>
                <th>Part</th>
                <th>Required</th>
                <th>Already there</th>
                <th>To move</th>
                <th>From</th>
                <th>Short</th>
              </tr>
            </thead>
            <tbody>
              {plan.lines.map(line => (
                <tr key={line.part_id}>
                  <td>{line.part_name}</td>
                  {/* Attrition-adjusted: this is `_required`, the same number
                      the shortage table above shows. */}
                  <td className="tabular-nums">{formatQuantity(line.required)}</td>
                  <td className="tabular-nums text-muted">{line.at_staging ? formatQuantity(line.at_staging) : "—"}</td>
                  <td className="tabular-nums">{line.moving ? formatQuantity(line.moving) : "—"}</td>
                  <td className="text-xs text-muted">
                    {line.sources.length === 0
                      ? "—"
                      : line.sources
                          .map(s => `${s.storage_location_name ?? "unassigned"} (${formatQuantity(s.quantity)})`)
                          .join(", ")}
                  </td>
                  <td className={`tabular-nums ${line.short_by ? "text-danger" : ""}`}>
                    {line.short_by ? formatQuantity(line.short_by) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="mt-3 flex items-center justify-between">
            <div className="text-sm text-muted">
              {plan.totals.moving > 0
                ? `${formatQuantityPhrase(plan.totals.moving)} will move to ${plan.storage_location_name}.`
                : `Nothing left to move — ${plan.storage_location_name} is already stocked.`}
              {plan.totals.short_lines > 0 && (
                <span className="text-danger">
                  {" "}
                  {plan.totals.short_lines} line(s) short by {formatQuantity(plan.totals.short_by)}; the
                  available stock still moves.
                </span>
              )}
            </div>
            <button
              className="btn-primary"
              disabled={busy || plan.totals.moving === 0}
              onClick={() => {
                setErr(null);
                kitMutation.mutate({ storage_location_id: stagingId });
              }}
            >
              {busy ? "Kitting…" : "Kit to staging"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
