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

  function setQuantity(rowId: string, qty: number) {
    setRows(prev =>
      prev.map(r => (r.rowId === rowId ? { ...r, quantity: Math.max(0, qty | 0) } : r))
    );
  }

  return { rows, setRows, seenSigs, seenMpns, removeRow, setQuantity };
}
