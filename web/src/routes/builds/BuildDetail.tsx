import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { stockReportKeys, useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import { formatDateTime, formatQuantity } from "@/lib/format";
import EntityHeader from "@/components/EntityHeader";
import AttachmentsPanel from "@/components/AttachmentsPanel";
import ActivityTimeline from "@/components/ActivityTimeline";
import { SourceBomButton } from "@/routes/projects/sourcing/SourceBomButton";
import PrintLabelButton from "@/routes/labels/PrintLabelButton";
import BuildStagesPanel from "./BuildStagesPanel";
import BuildKitPanel from "./BuildKitPanel";
import type { Build, BuildShortageRow, BuildStage, Part, Project, ProjectEntry, StorageLocation } from "@/types";

type DetailOut = { build: Build; shortage: BuildShortageRow[] };

type ConsumeRow = { part_id: string; quantity: number; storage_location_id?: string };

export default function BuildDetail() {
  const { buildId } = useParams<{ buildId: string }>();

  if (!buildId) {
    return <div className="text-red-600 text-sm p-4">Missing build id.</div>;
  }

  return <BuildDetailQuery key={buildId} buildId={buildId} />;
}

function BuildDetailQuery({ buildId }: { buildId: string }) {
  const { data, isError, error } = useQuery({
    queryKey: useWsKey("build", buildId),
    queryFn: ({ signal }) => api.get<DetailOut>(`/builds/${buildId}`, { signal }),
  });

  if (isError) return <div className="text-red-600 text-sm p-4">Failed to load build. {error instanceof ApiError ? error.userMessage : ""}</div>;
  if (!data) return <div className="text-muted">Loading…</div>;

  return <BuildDetailBody buildId={buildId} detail={data} />;
}

function BuildDetailBody({ buildId, detail }: { buildId: string; detail: DetailOut }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const { workspaceId } = useAuth();
  const { build, shortage } = detail;
  const projectId = build.project_id;

  const { data: project } = useQuery({
    queryKey: useWsKey("project", projectId),
    queryFn: ({ signal }) => api.get<Project>(`/projects/${projectId}`, { signal }),
  });
  const { data: entries } = useQuery({
    queryKey: useWsKey("project", projectId, "entries"),
    queryFn: ({ signal }) => api.get<ProjectEntry[]>(`/projects/${projectId}/entries`, { signal }),
  });
  const { data: parts } = useQuery({ queryKey: useWsKey("parts"), queryFn: ({ signal }) => api.get<Part[]>("/parts?limit=200", { signal }) });
  const { data: storage } = useQuery({ queryKey: useWsKey("storage"), queryFn: ({ signal }) => api.get<StorageLocation[]>("/storage", { signal }) });
  // Multi-stage builds (Track B2). Empty for a single-pass build.
  const { data: stagesData } = useQuery({
    queryKey: useWsKey("build", buildId, "stages"),
    queryFn: ({ signal }) => api.get<BuildStage[]>(`/builds/${buildId}/stages`, { signal }),
  });
  const stages = useMemo(() => stagesData ?? [], [stagesData]);

  // consumption plan: project_entry_id → list of consume rows
  const [plan, setPlan] = useState<Record<string, ConsumeRow[]>>({});
  const [outputLotName, setOutputLotName] = useState("");
  const [outputStorageLocationId, setOutputStorageLocationId] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const partsById = useMemo(() => new Map(parts?.map(p => [p.id, p]) ?? []), [parts]);

  type ConsumePayload = {
    lines: {
      project_entry_id: string;
      part_id: string;
      quantity: number;
      storage_location_id?: string;
    }[];
    output_storage_location_id?: string;
    output_lot_name?: string;
  };

  const consumeMutation = useApiMutation<unknown, ConsumePayload>({
    mutationKey: ["build", buildId, "consume"],
    mutationFn: (payload) => api.post(`/builds/${buildId}/consume`, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "build", buildId) });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "parts") });
      for (const queryKey of stockReportKeys(workspaceId)) {
        qc.invalidateQueries({ queryKey });
      }
      toast.success("Build complete — stock decremented.");
    },
    onError: (e) => {
      const m = e instanceof ApiError ? e.userMessage : "Build failed";
      setErr(m);
      toast.error(m);
    },
  });

  const archiveMutation = useApiMutation<unknown, { wasArchived: boolean }>({
    mutationKey: ["build", buildId, "archive"],
    mutationFn: ({ wasArchived }) =>
      api.post(`/builds/${buildId}/${wasArchived ? "restore" : "archive"}`),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "build", buildId) });
      qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "builds") });
      toast.success(vars.wasArchived ? "Build restored." : "Build archived.");
      if (!vars.wasArchived) nav("/builds");
    },
    onError: (e) => {
      toast.error(e instanceof ApiError ? e.userMessage : "Archive failed");
    },
  });


  const isEditable = build.status === "planned" || build.status === "in_progress";
  const shortageByEntry = new Map(shortage.map(s => [s.project_entry_id, s]));
  const reservationsActive = isEditable && !build.archived_at;
  const totalReserved = reservationsActive
    ? shortage.reduce((sum, s) => sum + s.required, 0)
    : 0;
  const reservedLines = reservationsActive ? shortage.length : 0;

  const busy = consumeMutation.isPending;

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

  function doConsume() {
    setErr(null);
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
    consumeMutation.mutate({
      lines,
      output_lot_name: outputLotName || undefined,
      output_storage_location_id: outputStorageLocationId || undefined,
    });
  }

  function doArchive() {
    archiveMutation.mutate({ wasArchived: !!build.archived_at });
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
        breadcrumb={
          project ? (
            <span>
              Projects · <span className="text-text">{project.name}</span> · Build
            </span>
          ) : undefined
        }
        subtitle={
          <span>
            <span className="pill">{build.status}</span>
            {build.archived_at && <span className="pill ml-2 bg-danger/20 text-danger">archived</span>}
            {reservationsActive && reservedLines > 0 && (
              <span className="ml-2 text-xs text-muted">
                {formatQuantity(totalReserved)} parts reserved across {reservedLines} line{reservedLines === 1 ? "" : "s"}
              </span>
            )}
          </span>
        }
        stats={[
          { label: "Quantity", value: build.quantity },
          {
            label: "Total short",
            value: formatQuantity(shortage.reduce((s, r) => s + r.short_by, 0)),
            tone: shortage.some(r => r.short_by > 0) ? "danger" : "success",
          },
          {
            label: "Status",
            value: build.status,
            tone: build.status === "complete" ? "success" : build.status === "cancelled" ? "danger" : "default",
          },
          ...(build.completed_at
            ? [{ label: "Completed", value: formatDateTime(build.completed_at) } as const]
            : []),
        ]}
        actions={
          <div className="flex gap-2">
            <PrintLabelButton entityType="build" entityId={build.id} entityName={build.name} />
            <SourceBomButton projectId={projectId} />
            {/* Track B4: the printable pick sheet. The per-stage sheets are
                reachable from the stage picker on that page, so this stays
                a single button. */}
            <Link className="btn" to={`/builds/${buildId}/pick-list`}>
              Pick list
            </Link>
            {isEditable && (
              <button className="btn" onClick={fillSuggested}>Auto-fill</button>
            )}
            <button className="btn" onClick={doArchive} disabled={archiveMutation.isPending}>
              {build.archived_at ? "Restore" : "Archive"}
            </button>
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
                <th>Attrition %</th>
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
                  <td className="tabular-nums text-muted">{s.attrition_pct ? `${s.attrition_pct}%` : "—"}</td>
                  {/* `required` already includes the attrition-inflated, ceil-rounded qty. */}
                  <td className="tabular-nums">{formatQuantity(s.required)}</td>
                  <td className="tabular-nums">{formatQuantity(s.available)}</td>
                  <td className="tabular-nums text-muted">{formatQuantity(s.substitute_available)}</td>
                  <td className={`tabular-nums ${s.short_by ? "text-danger" : ""}`}>{s.short_by ? formatQuantity(s.short_by) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <BuildStagesPanel
        buildId={buildId}
        build={build}
        stages={stages}
        entries={entries}
        partsById={partsById}
        storage={storage}
        isEditable={isEditable}
      />

      {/* Kitting (Track B3) — moves stock to a staging location; writes no
          consume rows, so it sits between the stage list and the
          consumption plan the operator uses once the tray is at the bench. */}
      <BuildKitPanel
        buildId={buildId}
        build={build}
        stages={stages}
        storage={storage}
        isEditable={isEditable}
      />

      {/* The whole-BOM plan is the single-pass path. Once the build has
          stages the server refuses this endpoint, so the card goes away and
          consumption happens stage by stage above. */}
      {isEditable && entries && stages.length === 0 && (
        <div className="card p-4 mb-4">
          <h3 className="text-md font-semibold mb-3">Consumption plan</h3>
          <div className="text-sm text-muted mb-2">
            One line per consumed part. Multiple lines per entry are allowed (e.g. main part + substitute).
            Use <strong>Auto-fill</strong> for a default plan, then tweak.
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
            <div>
              <label className="label" htmlFor="build-output-lot-name">Output lot name</label>
              <input
                id="build-output-lot-name"
                className="input"
                value={outputLotName}
                onChange={e => setOutputLotName(e.target.value)}
                placeholder={`${build.name}-out`}
              />
            </div>
            <div>
              <label className="label" htmlFor="build-output-storage">Output storage</label>
              <select
                id="build-output-storage"
                className="input"
                value={outputStorageLocationId}
                onChange={e => setOutputStorageLocationId(e.target.value)}
              >
                <option value="">— none —</option>
                {storage?.filter(s => !s.archived_at && !s.is_full).map(s => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </div>
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
                        <div className="text-xs text-muted">need {formatQuantity(s?.required, undefined, { fallback: "—" })}</div>
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
                        <div className="text-xs text-muted">need {formatQuantity(s?.required, undefined, { fallback: "—" })}</div>
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
                        step={1}
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

      <div className="grid lg:grid-cols-2 gap-4 mt-4">
        <AttachmentsPanel objectType="build" objectId={build.id} canWrite />
        <ActivityTimeline endpoint={`/builds/${build.id}/activity`} />
      </div>
    </div>
  );
}
