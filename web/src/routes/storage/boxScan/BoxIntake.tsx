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
import { useWsKey } from "@/lib/queryKeys";
import { boxScanApi, type IntakeBody } from "./api";
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
  const rowsRef = useRef(rows);
  rowsRef.current = rows;

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

  const addRow = useCallback(
    async (row: IntakeRow) => {
      setRows(rs => [row, ...rs]);
      try {
        const res = await boxScanApi.resolve(storageId, {
          mpn: row.mpn,
          ...(row.sig ? { bag_signature: row.sig, raw_bag_code: row.raw } : {}),
        });
        if (res.status === "not_found") {
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
          if (autoSave) await save(ready);
        }
      } catch (e) {
        patch(row.id, { phase: "error", error: errorText(e) });
      }
    },
    [autoSave, patch, save, storageId],
  );

  const onBag = useCallback(
    (b: ScannedBag) => {
      const dupe = b.sig ? rowsRef.current.find(r => r.sig === b.sig) : undefined;
      if (dupe) {
        setHighlight(dupe.id);
        toast.info("Bag already scanned — edit its count below");
        return;
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
        phase: "resolving",
      });
    },
    [addRow],
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
            onSave={choice => void save(r, choice)}
            onUpdateCount={() => void updateCount(r)}
            onUndo={() => void undo(r)}
            onDismiss={() => setRows(rs => rs.filter(x => x.id !== r.id))}
          />
        ))}
      </div>
    </div>
  );
}
