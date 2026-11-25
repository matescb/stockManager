/**
 * SM-008b — the pin/pad check on the part's CAD tab.
 *
 * Lists every finding of `GET /parts/{id}/eda/pin-pad` with its class
 * (exposed pad, NC pin, shell, mechanical, electrical). Only electrical
 * findings fail; the rest are warnings. An owner/admin signed in to the
 * app gets "Accept" per finding (electrical ones need a note) and
 * "Revoke"; the server refuses every API token and lesser role
 * regardless (`eda.review_requires_owner`), so `can_accept` only hides
 * the controls.
 *
 * An acceptance is pinned to the two files it was made on. Replace
 * either and it shows under "Accepted on earlier files" and excuses
 * nothing until someone accepts again.
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useApiMutation } from "@/lib/mutations";
import { useWsKey, wsKeyOf } from "@/lib/queryKeys";
import { useAuth } from "@/lib/auth";
import { PinPadCheckSchema, type PinPadCheck, type PinPadFinding } from "@/lib/schemas";
import { InlineQueryError } from "@/components/QueryStateBoundary";

type Kind = PinPadFinding["kind"];
type Klass = PinPadFinding["class"];

const VERDICT: Record<PinPadCheck["verdict"], [string, string]> = {
  pass: ["Pins and pads match", "pill bg-success/15 text-success"],
  pass_with_warnings: ["Pass with warnings", "pill bg-warning/10 text-warning"],
  fail: ["Mismatch", "pill bg-danger/15 text-danger"],
  "not-checkable": ["Not checkable", "pill"],
};

const CLASS_LABEL: Record<Klass, string> = {
  electrical: "electrical",
  exposed_pad: "exposed pad",
  nc_pin: "NC pin",
  shell: "shell",
  mechanical: "mechanical",
};

const KIND_LABEL: Record<Kind, string> = {
  pad_without_pin: "Pad without pin",
  pin_without_pad: "Pin without pad",
  duplicate_pin: "Duplicate pin",
};

const keyOf = (kind: Kind, number: string) => `${kind}:${number}`;

export function PartCadPinPad({ partId }: { partId: string }) {
  const qc = useQueryClient();
  const { workspaceId } = useAuth();
  const query = useQuery({
    queryKey: useWsKey("part", partId, "eda", "pin-pad"),
    queryFn: ({ signal }) =>
      api.parsed.get(`/parts/${partId}/eda/pin-pad`, PinPadCheckSchema, { signal }),
    enabled: Boolean(partId),
  });
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const refresh = () =>
    qc.invalidateQueries({ queryKey: wsKeyOf(workspaceId, "part", partId, "eda", "pin-pad") });
  const onError = (e: unknown) => {
    setErr(e instanceof ApiError ? e.userMessage : "Failed to update the acceptance");
    refresh();
  };

  const accept = useApiMutation<unknown, { kind: Kind; number: string }>({
    mutationKey: ["part", partId, "eda", "pin-pad", "accept"],
    mutationFn: ({ kind, number }) =>
      api.post(`/parts/${partId}/eda/pin-pad/accept`, {
        finding_kind: kind,
        number,
        note: notes[keyOf(kind, number)]?.trim() || null,
      }),
    onSuccess: () => {
      setErr(null);
      refresh();
    },
    onError,
  });
  const revoke = useApiMutation<unknown, { kind: Kind; number: string }>({
    mutationKey: ["part", partId, "eda", "pin-pad", "revoke"],
    mutationFn: ({ kind, number }) =>
      api.delete(
        `/parts/${partId}/eda/pin-pad/accept?finding_kind=${encodeURIComponent(kind)}` +
          `&number=${encodeURIComponent(number)}`,
      ),
    onSuccess: () => {
      setErr(null);
      refresh();
    },
    onError,
  });
  const busy = accept.isPending || revoke.isPending;

  const data = query.data;
  return (
    <section className="card p-4 space-y-3" aria-label="Pin/pad check">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="card-title">Pin/pad check</h3>
        {data && <span className={VERDICT[data.verdict][1]}>{VERDICT[data.verdict][0]}</span>}
      </div>
      <InlineQueryError query={query} label="pin/pad check" />
      {err && <div className="text-danger text-sm">{err}</div>}
      {data?.verdict === "not-checkable" && (
        <p className="text-sm text-muted">
          {data.detail ?? data.reason}. This is not a pass: nothing was compared.
        </p>
      )}
      {data && data.findings.classified.length > 0 && (
        <ul className="space-y-2">
          {data.findings.classified.map((f) => {
            const key = keyOf(f.kind, f.number);
            const needsNote = f.class === "electrical";
            const note = notes[key] ?? "";
            return (
              <li key={key} className="space-y-1" data-testid={`pin-pad-${key}`}>
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium">{KIND_LABEL[f.kind]}</span>
                  <code className="text-xs">{f.number}</code>
                  <span
                    className={
                      f.class === "electrical" ? "pill bg-danger/15 text-danger" : "pill"
                    }
                  >
                    {CLASS_LABEL[f.class]}
                  </span>
                  <span className="text-xs text-muted">{f.reason}</span>
                </div>
                {f.accepted ? (
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                    <span>
                      Accepted by {f.accepted.by_name ?? "someone"}, {formatDate(f.accepted.at)}
                      {f.accepted.note && ` — ${f.accepted.note}`}
                    </span>
                    {data.can_accept && (
                      <button
                        type="button"
                        className="btn-ghost btn-sm"
                        disabled={busy}
                        onClick={() => revoke.mutate({ kind: f.kind, number: f.number })}
                      >
                        Revoke
                      </button>
                    )}
                  </div>
                ) : (
                  data.can_accept && (
                    <div className="flex flex-wrap items-center gap-2">
                      <input
                        className="input flex-1 min-w-48"
                        aria-label={`Note for ${KIND_LABEL[f.kind]} ${f.number}`}
                        placeholder={needsNote ? "Why is this fine? (required)" : "Note (optional)"}
                        maxLength={2000}
                        value={note}
                        onChange={(e) => setNotes((prev) => ({ ...prev, [key]: e.target.value }))}
                      />
                      <button
                        type="button"
                        className="btn-primary btn-sm"
                        disabled={busy || (needsNote && !note.trim())}
                        onClick={() => accept.mutate({ kind: f.kind, number: f.number })}
                      >
                        Accept
                      </button>
                    </div>
                  )
                )}
              </li>
            );
          })}
        </ul>
      )}
      {data && data.stale_acceptances.length > 0 && (
        <div className="space-y-1">
          <div className="section-title">Accepted on earlier files</div>
          <ul className="space-y-1 text-xs text-muted">
            {data.stale_acceptances.map((a) => (
              <li
                key={`${keyOf(a.finding_kind, a.number)}:${a.symbol_sha256}:${a.footprint_sha256}`}
                className="flex flex-wrap items-center gap-2"
              >
                <span>
                  {KIND_LABEL[a.finding_kind]} {a.number} ({CLASS_LABEL[a.class]}) —{" "}
                  {a.by_name ?? "someone"}, {formatDate(a.at)}; the files have changed since,
                  so it no longer applies.
                </span>
                {data.can_accept && (
                  <button
                    type="button"
                    className="btn-ghost btn-sm"
                    disabled={busy}
                    onClick={() => revoke.mutate({ kind: a.finding_kind, number: a.number })}
                  >
                    Clear
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
