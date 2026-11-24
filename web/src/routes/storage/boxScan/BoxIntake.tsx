/**
 * Box intake: scan bags (or type an MPN) and each one lands in this box as
 * its own lot. Parts not in the library yet are created from the
 * workspace's provider; a bag recorded in another box is moved here.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { bagContext, noMpnMessage } from "@/lib/bagCode";
import { playNeedMoreInfo } from "@/components/scanner/scanSounds";
import { useWsKey } from "@/lib/queryKeys";
import { boxScanApi, type IntakeBody } from "./api";
import { firstMissing, parseFollowUpAnswer, type FollowUp } from "./followUp";
import FollowUpPrompt from "./FollowUpPrompt";
import IntakeRowCard from "./IntakeRowCard";
import { loadIntakeDraft, newRowId, saveIntakeDraft, type IntakeRow } from "./intakeRows";
import ScanPanel from "./ScanPanel";
import type { ScannedBag } from "./scanCode";

const AUTO_SAVE_PREF = "boxIntake:autoSave";

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.userMessage : "Request failed";
}

function readAutoSave(): boolean {
  try {
    return localStorage.getItem(AUTO_SAVE_PREF) !== "off";
  } catch {
    return true;
  }
}

export default function BoxIntake() {
  const { storageId = "" } = useParams();
  const qc = useQueryClient();
  const boxKey = useWsKey("storage", storageId);
  const [rows, setRows] = useState<IntakeRow[]>(() => loadIntakeDraft(storageId));
  const [autoSave, setAutoSave] = useState(readAutoSave);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [followUp, setFollowUp] = useState<FollowUp | null>(null);
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const followUpRef = useRef(followUp);
  followUpRef.current = followUp;

  useEffect(() => saveIntakeDraft(storageId, rows), [storageId, rows]);

  const patch = useCallback((id: string, p: Partial<IntakeRow>) => {
    setRows(rs => rs.map(r => (r.id === id ? { ...r, ...p } : r)));
  }, []);

  const refresh = useCallback(() => qc.invalidateQueries({ queryKey: boxKey }), [qc, boxKey]);

  const save = useCallback(
    async (row: IntakeRow, onExisting: "move" | "add_new" = "move") => {
      if (!(row.quantity > 0)) return;
      patch(row.id, { phase: "saving", error: undefined });
      const body: IntakeBody = {
        quantity: row.quantity,
        on_existing: onExisting,
        ...(row.resolve?.part ? { part_id: row.resolve.part.id } : { mpn: row.mpn }),
        ...(row.sig ? { bag_signature: row.sig, raw_bag_code: row.raw } : {}),
        ...(row.lotName ? { lot_name: row.lotName } : {}),
        ...(row.lotSerial ? { lot_serial: row.lotSerial } : {}),
        ...(row.comments ? { comments: row.comments } : {}),
      };
      try {
        const res = await boxScanApi.intake(storageId, body);
        patch(row.id, { phase: "saved", result: res, savedQuantity: row.quantity });
        void refresh();
      } catch (e) {
        patch(row.id, { phase: "error", error: errorText(e) });
      }
    },
    [patch, refresh, storageId],
  );

  /** Hold `row` as the one pending bag if a scanned label left something
   *  out, with the distinct "need more info" tone. */
  const askFollowUp = useCallback((row: IntakeRow) => {
    const missing = row.raw ? firstMissing(row) : null;
    if (!missing) return;
    setFollowUp({ rowId: row.id, missing, context: row.context ?? "" });
    setHighlight(row.id);
    playNeedMoreInfo();
  }, []);

  /** Ask the server what `row` is; a label without an MPN that matches no
   *  recorded bag waits for the operator to supply one. */
  const resolveRow = useCallback(
    async (row: IntakeRow) => {
      try {
        const res = await boxScanApi.resolve(storageId, {
          mpn: row.mpn,
          ...(row.sig ? { bag_signature: row.sig, raw_bag_code: row.raw } : {}),
        });
        if (res.status === "not_found" && !row.mpn) {
          patch(row.id, { phase: "needs_mpn", resolve: res });
          askFollowUp(row);
        } else if (res.status === "not_found") {
          patch(row.id, { phase: "error", resolve: res, error: `${row.mpn} is not in the library, and the parts provider has no match (or none is configured)` });
        } else if (res.status === "bag_elsewhere") {
          patch(row.id, { phase: "needs_choice", resolve: res });
        } else if (res.status === "bag_here" && res.bag) {
          const q = res.bag.quantity_here;
          patch(row.id, {
            phase: "saved",
            resolve: res,
            quantity: q,
            savedQuantity: q,
            result: { status: "already_here", lot_id: res.bag.lot_id, part: res.part!, part_created: false },
          });
        } else {
          const ready = { ...row, resolve: res, phase: "ready" as const };
          patch(row.id, { phase: "ready", resolve: res });
          if (!(row.quantity > 0)) askFollowUp(ready);
          else if (autoSave) await save(ready);
        }
      } catch (e) {
        patch(row.id, { phase: "error", error: errorText(e) });
      }
    },
    [askFollowUp, autoSave, patch, save, storageId],
  );

  const addRow = useCallback(
    async (row: IntakeRow) => {
      setRows(rs => [row, ...rs]);
      await resolveRow(row);
    },
    [resolveRow],
  );

  /** Complete a no-MPN row: keep its quantity, lot, order refs and the
   *  signature of the original 2D label. */
  const supplyMpn = useCallback(
    (row: IntakeRow, mpn: string) => {
      const next = { ...row, mpn, phase: "resolving" as const, error: undefined };
      patch(row.id, { mpn, phase: "resolving", error: undefined });
      void resolveRow(next);
    },
    [patch, resolveRow],
  );

  const cancelFollowUp = useCallback(() => setFollowUp(null), []);

  /** Apply a scanned or typed answer to the pending bag. Returns false when
   *  the input does not answer the question (the prompt stays). */
  const answerFollowUp = useCallback(
    (input: ScannedBag | string): boolean => {
      const pending = followUpRef.current;
      const row = pending && rowsRef.current.find(r => r.id === pending.rowId);
      if (!pending || !row) {
        setFollowUp(null);
        return false;
      }
      const answer = parseFollowUpAnswer(pending.missing, input);
      if (!answer) {
        toast.error(pending.missing === "quantity" ? "That is not a quantity — scan or type a number" : "That is not a part number");
        return false;
      }
      setFollowUp(null);
      if ("mpn" in answer) {
        supplyMpn(row, answer.mpn);
      } else {
        const next = { ...row, quantity: answer.quantity };
        patch(row.id, { quantity: answer.quantity });
        if (autoSave && row.phase === "ready") void save(next);
      }
      return true;
    },
    [autoSave, patch, save, supplyMpn],
  );

  const onBag = useCallback(
    (b: ScannedBag) => {
      const dupe = b.sig ? rowsRef.current.find(r => r.sig === b.sig) : undefined;
      if (dupe) {
        setHighlight(dupe.id);
        toast.info("Bag already scanned — edit its count below");
        return;
      }
      if (followUpRef.current) {
        // A plain scan answers the pending bag. A new 2D label abandons the
        // prompt (the bag keeps its inline entry box) and is its own bag.
        if (!b.raw.trim().startsWith("[)>")) {
          answerFollowUp(b);
          return;
        }
        setFollowUp(null);
        toast.info("The previous bag still needs its part number — finish it in the list");
      }
      void addRow({
        id: newRowId(),
        mpn: b.bag.mpn.trim(),
        quantity: b.bag.quantity ?? 0,
        sig: b.sig,
        raw: b.raw,
        lotName: b.lotName,
        lotSerial: b.bag.serial ?? null,
        comments: b.comments,
        context: bagContext(b.bag),
        ...(b.bag.noMpn ? { noMpnMessage: noMpnMessage(b.bag) } : {}),
        phase: "resolving",
      });
    },
    [addRow, answerFollowUp],
  );

  const onManual = useCallback(
    (mpn: string, quantity: number) => void addRow({ id: newRowId(), mpn, quantity, phase: "resolving" }),
    [addRow],
  );

  async function updateCount(row: IntakeRow, quantity = row.quantity) {
    const part = row.result?.part;
    if (!part || !row.result?.lot_id) return;
    patch(row.id, { phase: "saving" });
    try {
      await boxScanApi.setCount(storageId, part.id, row.result.lot_id, quantity);
      patch(row.id, { phase: "saved", quantity, savedQuantity: quantity });
      void refresh();
    } catch (e) {
      patch(row.id, { phase: "error", error: errorText(e) });
    }
  }

  async function undo(row: IntakeRow) {
    await updateCount(row, 0);
    setRows(rs => rs.filter(x => x.id !== row.id || x.phase === "error"));
  }

  function toggleAutoSave(v: boolean) {
    setAutoSave(v);
    try {
      localStorage.setItem(AUTO_SAVE_PREF, v ? "on" : "off");
    } catch {
      /* not remembered */
    }
  }

  const saved = rows.filter(r => r.phase === "saved");
  const pending = rows.filter(r => r.phase === "ready");

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <ScanPanel boxId={storageId} mode="intake" onBag={onBag} onManual={onManual} />
      <div className="space-y-3">
        {followUp && <FollowUpPrompt followUp={followUp} onAnswer={t => void answerFollowUp(t)} onCancel={cancelFollowUp} />}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm text-muted">
            {saved.length} saved · {pending.length} waiting · {rows.length} scanned
          </div>
          <div className="flex items-center gap-3">
            <label className="text-sm inline-flex items-center gap-1.5">
              <input type="checkbox" checked={autoSave} onChange={e => toggleAutoSave(e.target.checked)} />
              Save each scan immediately
            </label>
            {pending.length > 0 && (
              <button type="button" className="btn-primary btn-sm" onClick={() => pending.forEach(r => void save(r))}>
                Save all ({pending.length})
              </button>
            )}
            {rows.length > 0 && (
              <button type="button" className="btn btn-sm" onClick={() => setRows(rs => rs.filter(r => r.phase !== "saved"))}>
                Clear saved
              </button>
            )}
          </div>
        </div>
        {rows.length === 0 && (
          <div className="card p-6 text-center text-muted text-sm">
            Scan a bag's 2D code, or add a part by MPN. Each bag is stored in this box as its own lot.
          </div>
        )}
        {rows.map(r => (
          <IntakeRowCard
            key={r.id}
            row={r}
            highlighted={highlight === r.id}
            onQuantity={q => patch(r.id, { quantity: q })}
            onMpn={mpn => {
              if (followUp?.rowId === r.id) setFollowUp(null);
              supplyMpn(r, mpn);
            }}
            onSave={choice => void save(r, choice)}
            onUpdateCount={() => void updateCount(r)}
            onUndo={() => void undo(r)}
            onDismiss={() => {
              if (followUp?.rowId === r.id) setFollowUp(null);
              setRows(rs => rs.filter(x => x.id !== r.id));
            }}
          />
        ))}
      </div>
    </div>
  );
}
