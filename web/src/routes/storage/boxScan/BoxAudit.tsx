/**
 * Box audit: scan the bags that are physically in the box. Each scan ticks
 * off a recorded bag (count editable); bags never scanned are listed at the
 * bottom with a verdict — write off (default), move to another box, or
 * keep. "Finish audit" applies everything in one transaction.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useWsKey } from "@/lib/queryKeys";
import { formatQuantity } from "@/lib/format";
import { useConfirm } from "@/components/ConfirmDialog";
import { noMpnMessage } from "@/lib/bagCode";
import type { StorageLocation } from "@/types";
import { boxScanApi, bucketKey, type BoxBag } from "./api";
import AuditExtras, { type ExtraBag } from "./AuditExtras";
import {
  DEFAULT_MISSING,
  EMPTY_DRAFT,
  findBySignature,
  loadAuditDraft,
  saveAuditDraft,
  splitBags,
  summarize,
  type AuditDraft,
  type MissingAction,
} from "./auditState";
import { newRowId } from "./intakeRows";
import ScanPanel from "./ScanPanel";
import { isPlainMpnScan, type ScannedBag } from "./scanCode";

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.userMessage : "Request failed";
}

function BagLabel({ b }: { b: BoxBag }) {
  return (
    <div className="min-w-0 flex-1">
      <div className="font-medium truncate">
        {b.part ? <Link to={`/parts/${b.part.id}/stock`} className="hover:underline">{b.part.name}</Link> : "Unknown part"}
      </div>
      <div className="text-xs text-muted truncate">
        {[b.part?.mpn, b.part?.manufacturer].filter(Boolean).join(" · ")}
        {" · "}
        {b.lot ? b.lot.name ?? "lot" : <span className="text-warning">old record, not tracked per bag</span>}
      </div>
    </div>
  );
}

export default function BoxAudit() {
  const { storageId = "" } = useParams();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const boxKey = useWsKey("storage", storageId);
  const bagsKey = useWsKey("storage", storageId, "bags");
  const bagsQuery = useQuery({ queryKey: bagsKey, queryFn: ({ signal }) => boxScanApi.bags(storageId, signal) });
  const boxesQuery = useQuery({
    queryKey: useWsKey("storage", "list"),
    queryFn: ({ signal }) => api.get<StorageLocation[]>("/storage?limit=1000", { signal }),
  });
  const [draft, setDraft] = useState<AuditDraft>(() => loadAuditDraft(storageId));
  const [extras, setExtras] = useState<ExtraBag[]>([]);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [finishing, setFinishing] = useState(false);
  const bags = useMemo(() => bagsQuery.data ?? [], [bagsQuery.data]);
  const bagsRef = useRef(bags);
  bagsRef.current = bags;
  const extrasRef = useRef(extras);
  extrasRef.current = extras;

  useEffect(() => saveAuditDraft(storageId, draft), [storageId, draft]);
  useEffect(() => {
    if (!highlight) return;
    document.getElementById(`bag-${highlight}`)?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [highlight]);

  const markScanned = useCallback((b: BoxBag, quantity = b.quantity) => {
    const k = bucketKey(b);
    setDraft(d => (k in d.counted ? d : { ...d, counted: { ...d.counted, [k]: quantity } }));
    setHighlight(k);
  }, []);

  const unmark = (k: string) =>
    setDraft(d => {
      const counted = { ...d.counted };
      delete counted[k];
      return { ...d, counted };
    });

  const setMissing = (k: string, m: MissingAction) => setDraft(d => ({ ...d, missing: { ...d.missing, [k]: m } }));
  const patchExtra = useCallback(
    (id: string, p: Partial<ExtraBag>) => setExtras(xs => xs.map(x => (x.id === id ? { ...x, ...p } : x))),
    [],
  );

  /** Look a not-recorded bag up (library only — an audit never spends
   *  provider quota). A label without an MPN waits for one. */
  const resolveExtra = useCallback(
    async (x: ExtraBag) => {
      try {
        const res = await boxScanApi.resolve(storageId, {
          mpn: x.mpn,
          provider_lookup: false,
          ...(x.sig ? { bag_signature: x.sig, raw_bag_code: x.raw } : {}),
        });
        patchExtra(x.id, {
          resolve: res,
          busy: false,
          needsMpn: res.status === "not_found" && !x.mpn,
          quantity: x.quantity || res.bag?.locations[0]?.quantity || 0,
        });
      } catch (e) {
        patchExtra(x.id, { busy: false, error: errorText(e) });
      }
    },
    [patchExtra, storageId],
  );

  const supplyMpn = useCallback(
    (x: ExtraBag, mpn: string) => {
      patchExtra(x.id, { mpn, busy: true, needsMpn: false, error: undefined });
      void resolveExtra({ ...x, mpn });
    },
    [patchExtra, resolveExtra],
  );

  const onBag = useCallback(
    async (s: ScannedBag) => {
      const known = findBySignature(bagsRef.current, s.sig);
      if (known) {
        if (bucketKey(known) in draft.counted) toast.info("Already scanned");
        markScanned(known);
        return;
      }
      if (s.sig && extrasRef.current.some(x => x.sig === s.sig)) {
        toast.info("Already scanned");
        return;
      }
      const waiting = isPlainMpnScan(s) ? extrasRef.current.find(x => x.needsMpn) : undefined;
      if (waiting) {
        supplyMpn(waiting, s.bag.mpn.trim());
        return;
      }
      const extra: ExtraBag = {
        id: newRowId(),
        mpn: s.bag.mpn.trim(),
        quantity: s.bag.quantity ?? 0,
        sig: s.sig,
        raw: s.raw,
        lotName: s.lotName,
        lotSerial: s.bag.serial ?? null,
        comments: s.comments,
        ...(s.bag.noMpn ? { noMpnMessage: noMpnMessage(s.bag) } : {}),
        busy: true,
      };
      setExtras(xs => [extra, ...xs]);
      await resolveExtra(extra);
    },
    [draft.counted, markScanned, resolveExtra, supplyMpn],
  );

  async function addExtra(x: ExtraBag) {
    patchExtra(x.id, { busy: true, error: undefined });
    try {
      const res = await boxScanApi.intake(storageId, {
        quantity: x.quantity,
        ...(x.resolve?.part ? { part_id: x.resolve.part.id } : { mpn: x.mpn }),
        ...(x.sig ? { bag_signature: x.sig, raw_bag_code: x.raw } : {}),
        ...(x.lotName ? { lot_name: x.lotName } : {}),
        ...(x.lotSerial ? { lot_serial: x.lotSerial } : {}),
        ...(x.comments ? { comments: x.comments } : {}),
      });
      setDraft(d => ({ ...d, counted: { ...d.counted, [`lot:${res.lot_id}`]: x.quantity } }));
      setExtras(xs => xs.filter(e => e.id !== x.id));
      await qc.invalidateQueries({ queryKey: boxKey });
    } catch (e) {
      patchExtra(x.id, { busy: false, error: errorText(e) });
    }
  }

  const { scanned, unscanned } = splitBags(bags, draft);
  const summary = summarize(bags, draft);
  const moveWithoutTarget = unscanned.some(b => {
    const m = draft.missing[bucketKey(b)];
    return m?.action === "move" && !m.to;
  });
  const otherBoxes = (boxesQuery.data ?? []).filter(s => s.id !== storageId);

  async function finish() {
    const ok = await confirm({
      title: "Finish audit",
      message:
        `${scanned.length} of ${bags.length} bags scanned. ` +
        `${summary.corrected} count corrections, ${summary.writeOff} write-offs, ${summary.move} moves.`,
      confirmLabel: "Apply",
      severity: summary.writeOff > 0 ? "warning" : undefined,
    });
    if (!ok) return;
    setFinishing(true);
    try {
      if (summary.lines.length > 0) await boxScanApi.audit(storageId, summary.lines);
      toast.success("Audit applied");
      setDraft(EMPTY_DRAFT);
      setExtras([]);
      await qc.invalidateQueries({ queryKey: boxKey });
    } catch (e) {
      toast.error(errorText(e));
    } finally {
      setFinishing(false);
    }
  }

  async function restart() {
    if (!(await confirm({ title: "Start over", message: "Forget what has been scanned in this audit?", confirmLabel: "Start over" }))) return;
    setDraft(EMPTY_DRAFT);
    setExtras([]);
  }

  if (bagsQuery.isError) return <div className="text-danger text-sm p-4">Failed to load box contents. {errorText(bagsQuery.error)}</div>;

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <ScanPanel boxId={storageId} mode="audit" onBag={s => void onBag(s)} />
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm">
            <strong>{scanned.length}</strong> of {bags.length} bags scanned
            {unscanned.length > 0 && <span className="text-danger"> · {unscanned.length} not scanned</span>}
          </div>
          <div className="flex gap-2">
            <button type="button" className="btn btn-sm inline-flex items-center gap-1" onClick={() => void restart()}>
              <RotateCcw size={14} /> Start over
            </button>
            <button
              type="button"
              className="btn-primary btn-sm"
              disabled={finishing || bagsQuery.isLoading || moveWithoutTarget || bags.length === 0}
              title={moveWithoutTarget ? "Pick a destination for every bag to move" : undefined}
              onClick={() => void finish()}
            >
              Finish audit
            </button>
          </div>
        </div>

        <AuditExtras
          extras={extras}
          onQuantity={(id, q) => patchExtra(id, { quantity: q })}
          onAdd={x => void addExtra(x)}
          onMpn={supplyMpn}
          onDismiss={id => setExtras(xs => xs.filter(x => x.id !== id))}
        />

        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="section-title text-danger">Not scanned ({unscanned.length})</h3>
            {unscanned.length > 1 && (
              <div className="flex gap-2">
                <button type="button" className="btn btn-sm" onClick={() => unscanned.forEach(b => setMissing(bucketKey(b), { action: "write_off" }))}>
                  All: write off
                </button>
                <button type="button" className="btn btn-sm" onClick={() => unscanned.forEach(b => setMissing(bucketKey(b), { action: "keep" }))}>
                  All: keep
                </button>
              </div>
            )}
          </div>
          {bagsQuery.isLoading && <div className="text-muted text-sm">Loading…</div>}
          {!bagsQuery.isLoading && unscanned.length === 0 && (
            <div className="card p-4 text-sm text-success inline-flex items-center gap-2 w-full">
              <CheckCircle2 size={16} /> Every recorded bag has been scanned.
            </div>
          )}
          {unscanned.map(b => {
            const k = bucketKey(b);
            const m = draft.missing[k] ?? DEFAULT_MISSING;
            return (
              <div key={k} id={`bag-${k}`} className="card p-3 flex flex-col gap-2 sm:flex-row sm:items-center border-danger/50 bg-danger/5">
                <CircleDashed size={16} className="text-danger shrink-0 hidden sm:block" />
                <BagLabel b={b} />
                <div className="tabular-nums text-sm shrink-0">{formatQuantity(b.quantity)} pcs</div>
                <div className="flex items-center gap-2 shrink-0">
                  <label className="sr-only" htmlFor={`act-${k}`}>Action</label>
                  <select
                    id={`act-${k}`}
                    className="input w-32"
                    value={m.action}
                    onChange={e => setMissing(k, { action: e.target.value as MissingAction["action"], to: m.to })}
                  >
                    <option value="write_off">Write off</option>
                    <option value="move">Move to…</option>
                    <option value="keep">Keep</option>
                  </select>
                  {m.action === "move" && (
                    <select
                      aria-label="Destination box"
                      className="input w-40"
                      value={m.to ?? ""}
                      onChange={e => setMissing(k, { action: "move", to: e.target.value || undefined })}
                    >
                      <option value="">Pick a box…</option>
                      {otherBoxes.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                    </select>
                  )}
                  <button type="button" className="btn btn-sm" onClick={() => markScanned(b)}>It's here</button>
                </div>
              </div>
            );
          })}
        </section>

        <section className="space-y-2">
          <h3 className="section-title text-success">Scanned ({scanned.length})</h3>
          {scanned.map(b => {
            const k = bucketKey(b);
            const counted = draft.counted[k];
            return (
              <div key={k} id={`bag-${k}`} className={`card p-3 flex flex-col gap-2 sm:flex-row sm:items-center ${highlight === k ? "ring-2 ring-accent" : ""}`}>
                <CheckCircle2 size={16} className="text-success shrink-0 hidden sm:block" />
                <BagLabel b={b} />
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-xs text-muted">recorded {formatQuantity(b.quantity)}</span>
                  <label className="sr-only" htmlFor={`count-${k}`}>Counted quantity</label>
                  <input
                    id={`count-${k}`}
                    type="number"
                    min={0}
                    step={1}
                    className={`input w-24 tabular-nums ${counted !== b.quantity ? "border-warning" : ""}`}
                    value={counted}
                    onChange={e => setDraft(d => ({ ...d, counted: { ...d.counted, [k]: Math.max(0, Math.floor(Number(e.target.value) || 0)) } }))}
                  />
                  <button type="button" className="btn btn-sm" onClick={() => unmark(k)}>Not here</button>
                </div>
              </div>
            );
          })}
        </section>
      </div>
    </div>
  );
}
