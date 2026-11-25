/**
 * Part → Substitutes tab.
 *
 * SM-012: each link shows its relation (pin-compatible, footprint-compatible,
 * functional) and evaluation state (not-evaluated, evaluated-ok, rejected)
 * with the evidence reference and who/when last changed it. "Edit" opens a
 * small inline editor that PATCHes `/parts/{id}/substitutes/{sub}`. Only
 * evaluated-ok substitutes count in the shortage views'
 * `short_by_evaluated_only`.
 */
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import QueryStateBoundary from "@/components/QueryStateBoundary";
import type { Part } from "@/types";

export type SubstituteRelation = "pin-compatible" | "footprint-compatible" | "functional";
export type SubstituteEvaluation = "not-evaluated" | "evaluated-ok" | "rejected";

export type Sub = {
  part_id: string;
  direction: string;
  relation: SubstituteRelation | null;
  evaluation: SubstituteEvaluation;
  evidence: string | null;
  evaluated_by: string | null;
  evaluated_at: string | null;
};

const RELATIONS: [SubstituteRelation, string][] = [
  ["pin-compatible", "Pin-compatible"],
  ["footprint-compatible", "Footprint-compatible"],
  ["functional", "Functional"],
];

const EVALUATIONS: [SubstituteEvaluation, string][] = [
  ["not-evaluated", "Not evaluated"],
  ["evaluated-ok", "Evaluated OK"],
  ["rejected", "Rejected"],
];

const EVALUATION_CLASS: Record<SubstituteEvaluation, string> = {
  "not-evaluated": "pill",
  "evaluated-ok": "pill bg-success/15 text-success",
  rejected: "pill bg-danger/15 text-danger",
};

function label<T extends string>(options: [T, string][], value: T | null): string | null {
  return options.find(([v]) => v === value)?.[1] ?? null;
}

type Draft = { relation: string; evaluation: SubstituteEvaluation; evidence: string };

function SubstituteEditor({
  sub,
  onSave,
  onCancel,
}: {
  sub: Sub;
  onSave: (draft: Draft) => Promise<void>;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<Draft>({
    relation: sub.relation ?? "",
    evaluation: sub.evaluation,
    evidence: sub.evidence ?? "",
  });
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex flex-wrap items-end gap-2 mt-2" data-testid={`sub-editor-${sub.part_id}`}>
      <label className="text-xs">
        <span className="label">Relation</span>
        <select
          className="input"
          aria-label="Relation"
          value={draft.relation}
          onChange={e => setDraft({ ...draft, relation: e.target.value })}
        >
          <option value="">Not stated</option>
          {RELATIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </label>
      <label className="text-xs">
        <span className="label">Evaluation</span>
        <select
          className="input"
          aria-label="Evaluation"
          value={draft.evaluation}
          onChange={e => setDraft({ ...draft, evaluation: e.target.value as SubstituteEvaluation })}
        >
          {EVALUATIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </label>
      <label className="text-xs flex-1 min-w-[10rem]">
        <span className="label">Evidence</span>
        <input
          className="input w-full"
          aria-label="Evidence"
          maxLength={500}
          placeholder="e.g. SIM-012"
          value={draft.evidence}
          onChange={e => setDraft({ ...draft, evidence: e.target.value })}
        />
      </label>
      <button
        className="btn-primary btn-sm"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await onSave(draft);
          } finally {
            setBusy(false);
          }
        }}
      >
        Save
      </button>
      <button className="btn btn-sm" onClick={onCancel} disabled={busy}>Cancel</button>
    </div>
  );
}

export default function PartSubstitutes() {
  const { partId } = useParams();
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const subsQuery = useQuery({ queryKey: useWsKey("part", partId, "subs"), queryFn: ({ signal }) => api.get<Sub[]>(`/parts/${partId}/substitutes`, { signal }) });
  const { data: subs } = subsQuery;
  const { data: parts } = useQuery({ queryKey: useWsKey("parts"), queryFn: ({ signal }) => api.get<Part[]>("/parts?limit=200", { signal }) });
  const partsById = new Map(parts?.map(p => [p.id, p]) ?? []);
  const [pick, setPick] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", partId, "subs") });

  async function add() {
    if (!pick) return;
    await api.post(`/parts/${partId}/substitutes`, { substitute_part_id: pick });
    setPick("");
    refresh();
  }
  async function remove(id: string) {
    await api.delete(`/parts/${partId}/substitutes/${id}`);
    refresh();
  }
  async function save(id: string, draft: Draft) {
    try {
      await api.patch(`/parts/${partId}/substitutes/${id}`, {
        relation: draft.relation || null,
        evaluation: draft.evaluation,
        evidence: draft.evidence.trim() || null,
      });
      setErr(null);
      setEditing(null);
      refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save the evaluation.");
    }
  }

  return (
    <div className="card p-4 max-w-3xl">
      <h3 className="card-title mb-2">Substitutes</h3>
      <p className="text-muted text-xs mb-2">
        A substitute counts as interchangeable in shortage checks only once it is evaluated OK.
      </p>
      {err && <p className="text-danger text-sm mb-2" role="alert">{err}</p>}
      <QueryStateBoundary query={subsQuery} resourceLabel="substitutes">
      <ul className="space-y-2 mb-3">
        {(subs ?? []).map(s => {
          const p = partsById.get(s.part_id);
          const relation = label(RELATIONS, s.relation);
          return (
            <li key={s.part_id} className="text-sm">
              <div className="flex items-center justify-between gap-2">
                <span>
                  {p?.name ?? s.part_id}
                  {p?.mpn && <span className="text-muted ml-2">{p.mpn}</span>}
                </span>
                <span className="flex items-center gap-2">
                  {relation && <span className="pill">{relation}</span>}
                  <span className={EVALUATION_CLASS[s.evaluation]}>{label(EVALUATIONS, s.evaluation)}</span>
                  <button className="btn btn-sm" onClick={() => setEditing(editing === s.part_id ? null : s.part_id)}>Edit</button>
                  <button className="btn-danger btn-sm" onClick={() => remove(s.part_id)}>Remove</button>
                </span>
              </div>
              {(s.evidence || s.evaluated_at) && (
                <div className="text-muted text-xs">
                  {s.evidence && <>Evidence: {s.evidence}</>}
                  {s.evidence && s.evaluated_at && " · "}
                  {s.evaluated_at && <>updated {formatDate(s.evaluated_at)}</>}
                </div>
              )}
              {editing === s.part_id && (
                <SubstituteEditor
                  sub={s}
                  onSave={draft => save(s.part_id, draft)}
                  onCancel={() => setEditing(null)}
                />
              )}
            </li>
          );
        })}
        {(!subs || subs.length === 0) && <li className="text-muted text-sm">No substitutes.</li>}
      </ul>
      <div className="flex gap-2">
        <select className="input" value={pick} onChange={e => setPick(e.target.value)}>
          <option value="">Pick a part…</option>
          {parts?.filter(p => p.id !== partId).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button className="btn-primary" onClick={add}>Add</button>
      </div>
      </QueryStateBoundary>
    </div>
  );
}
