/**
 * useScanImportRows — manages the mutable scan-queue state that the parent
 * orchestrator passes down to ScanImportQueue and ScanImportActions.
 *
 * Encapsulates:
 *  - rows state + setRows
 *  - seenSigs / seenMpns dedup refs
 *  - removeRow / setQuantity helpers
 *  - draft restore (loadDraft) + initial seenSigs/seenMpns rebuild
 */
import { useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { loadDraft } from "./storage";
import { type Row } from "./types";

export function useScanImportRows() {
  const { workspaceId } = useAuth();

  // Dedup refs — keyed by signature first, then MPN.
  const seenSigs = useRef<Set<string>>(new Set());
  const seenMpns = useRef<Set<string>>(new Set());

  // Lazy initialiser: restore draft from sessionStorage.
  const [rows, setRows] = useState<Row[]>(() => {
    if (!workspaceId) return [];
    const restored = loadDraft(workspaceId);
    if (!restored) return [];
    for (const r of restored) {
      if (r.bagSig) seenSigs.current.add(r.bagSig);
      seenMpns.current.add(r.bag.mpn);
    }
    return restored;
  });

  function removeRow(rowId: string) {
    setRows(prev => {
      const dropped = prev.find(r => r.rowId === rowId);
      if (dropped) {
        seenMpns.current.delete(dropped.bag.mpn);
        if (dropped.bagSig) seenSigs.current.delete(dropped.bagSig);
      }
      return prev.filter(r => r.rowId !== rowId);
    });
  }

  /**
   * Store a row's quantity as-is, clamped at zero.
   *
   * This used to be `Math.max(0, qty | 0)`. `| 0` is JavaScript's ToInt32,
   * and it was wrong twice over:
   *
   *  - **It truncated.** A bag whose `Q` field says 12.5 became 12, and 12
   *    is what the operator then posted to `bulk-import-from-scan` — a
   *    wrong number, silently, on a write path. Quantity columns are
   *    `Numeric(18, 6)` since alembic 0074 (units-of-measure track).
   *  - **It wrapped at 2^31.** Typing 3000000000 gave -1294967296, which
   *    the `Math.max(0, …)` then clamped to 0 — so an over-large quantity
   *    silently became *no stock at all* instead of being rejected. That
   *    one is a live bug today, with nothing fractional required.
   *
   * The truncation was also redundant: this is only ever called from the
   * queue's integer-only number input, which already parses to an
   * integer. Dropping `| 0` changes nothing about what that input can
   * produce — it just stops mangling anything it doesn't.
   */
  function setQuantity(rowId: string, qty: number) {
    const clamped = Number.isFinite(qty) ? Math.max(0, qty) : 0;
    setRows(prev =>
      prev.map(r => (r.rowId === rowId ? { ...r, quantity: clamped } : r))
    );
  }

  return { rows, setRows, seenSigs, seenMpns, removeRow, setQuantity };
}
