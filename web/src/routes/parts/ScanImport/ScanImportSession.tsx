/**
 * ScanImportSession — camera viewport + per-scan lookup pipeline.
 *
 * Responsibilities:
 *  - Render the <Scanner> with module-level SCAN_IMPORT_SYMBOLOGIES (FE CRIT-2:
 *    keeping the array module-level prevents the multi-MB SDK from being torn
 *    down on every parent re-render).
 *  - Run the per-scan pipeline: dedup by sig/MPN → bag-rescan check →
 *    duplicate-MPN check → provider lookup with retry-with-backoff.
 *  - Emits new rows via `onRow(row)` and state updates via
 *    `onLookupUpdate(rowId, nextState)`.
 *
 * It does NOT own `rows` state — the parent (ScanImport) owns it.
 */
import { useCallback, useState } from "react";
import type React from "react";
import { ClipboardPaste, Plus } from "lucide-react";
import Scanner, { type ScanResult } from "@/components/scanner/Scanner";
import { api, ApiError } from "@/lib/api";
import { parseBagCode, bagSignature } from "@/lib/bagCode";
import type { MpnLookupResult, Part } from "@/types";
import {
  SCAN_IMPORT_SYMBOLOGIES,
  newRowId,
  type LookupState,
  type Row,
} from "./types";

// ─── transient-failure helpers ───────────────────────────────────────────────

function isTransientLookupFailure(err: unknown): boolean {
  if (err instanceof ApiError) {
    if (err.status >= 500) return true;
    const msg = (err.message || "").toLowerCase();
    return /unavailable|timeout|rate.?limit|temporar|503|504|502|connection/.test(msg);
  }
  if (err instanceof TypeError) return true; // network abort
  return false;
}

function isTransientResultMessage(msg: string | undefined | null): boolean {
  if (!msg) return false;
  return /unavailable|timeout|rate.?limit|temporar|service.?unavailable/i.test(msg);
}

const RETRY_DELAYS_MS = [800, 1600, 3000];

async function lookupMpnWithRetry(mpn: string): Promise<MpnLookupResult> {
  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
    try {
      const res = await api.post<MpnLookupResult>("/parts/lookup-mpn", { mpn });
      if (!res.found && isTransientResultMessage(res.message)) {
        if (attempt < RETRY_DELAYS_MS.length) {
          await new Promise(r => setTimeout(r, RETRY_DELAYS_MS[attempt]));
          continue;
        }
      }
      return res;
    } catch (err) {
      if (attempt < RETRY_DELAYS_MS.length && isTransientLookupFailure(err)) {
        await new Promise(r => setTimeout(r, RETRY_DELAYS_MS[attempt]));
        continue;
      }
      throw err;
    }
  }
  /* istanbul ignore next */
  throw new Error("lookupMpnWithRetry: unreachable");
}

// ─── component ───────────────────────────────────────────────────────────────

interface ScanImportSessionProps {
  seenSigs: React.MutableRefObject<Set<string>>;
  seenMpns: React.MutableRefObject<Set<string>>;
  onRow: (row: Row) => void;
  onLookupUpdate: (rowId: string, next: LookupState) => void;
}

export default function ScanImportSession({
  seenSigs,
  seenMpns,
  onRow,
  onLookupUpdate,
}: ScanImportSessionProps) {
  const [manualOpen, setManualOpen] = useState(false);
  const [manualCode, setManualCode] = useState("");
  const [manualSubmitting, setManualSubmitting] = useState(false);

  const handleScan = useCallback(
    async (s: ScanResult) => {
      const parsed = parseBagCode(s.data);
      const sig = await bagSignature(s.data);
      const mpn = parsed.mpn.trim();
      if (!mpn) return;

      // Dedup by signature first, MPN as fallback.
      if (sig && seenSigs.current.has(sig)) return;
      if (!sig && seenMpns.current.has(mpn)) return;
      if (sig) seenSigs.current.add(sig);
      seenMpns.current.add(mpn);

      const rowId = newRowId();
      const initialQty = parsed.quantity ?? 0;
      onRow({ rowId, bag: parsed, bagSig: sig, quantity: initialQty, state: { kind: "pending" } });

      const setState = (next: LookupState) => onLookupUpdate(rowId, next);

      try {
        // 1. Bag-rescan check.
        if (sig) {
          const prior = await api.get<{
            part_id: string;
            lot_id: string | null;
            storage_location_id: string | null;
            quantity: number;
          } | null>(`/parts/by-bag-signature/${sig}`);
          if (prior) {
            setState({
              kind: "bag_rescan",
              part_id: prior.part_id,
              lot_id: prior.lot_id,
              storage_location_id: prior.storage_location_id,
              quantity: prior.quantity,
            });
            return;
          }
        }

        // 2. Same-MPN duplicate.
        const dupes = await api.get<Part[]>(`/parts?mpn=${encodeURIComponent(mpn)}`);
        if (dupes.length > 0) {
          setState({ kind: "duplicate", existing: dupes[0] });
          return;
        }

        // 3. Provider lookup with auto-retry.
        const lookup = await lookupMpnWithRetry(mpn);
        if (lookup.found && lookup.result) {
          setState({ kind: "found", result: lookup.result, provider: lookup.provider });
        } else {
          setState({ kind: "error", message: lookup.message || "no match" });
        }
      } catch (e) {
        setState({
          kind: "error",
          message: e instanceof ApiError ? e.userMessage : "Lookup failed",
        });
      }
    },
    // seenSigs / seenMpns are refs; onRow / onLookupUpdate are stable callbacks.
    [seenSigs, seenMpns, onRow, onLookupUpdate],
  );

  async function handleManualSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = manualCode.trim();
    if (!data) return;
    setManualSubmitting(true);
    try {
      await handleScan({ data, symbology: "DataMatrix" });
      setManualCode("");
    } finally {
      setManualSubmitting(false);
    }
  }

  return (
    <div className="card p-3">
      <div className="mb-3 rounded-md border border-border bg-panel2/40">
        <button
          type="button"
          className="w-full px-3 py-2 text-left text-sm font-medium inline-flex items-center gap-2"
          aria-expanded={manualOpen}
          aria-controls="scan-import-manual-entry"
          onClick={() => setManualOpen(open => !open)}
        >
          <ClipboardPaste size={16} className="text-muted" />
          Manual entry
        </button>
        {manualOpen && (
          <form
            id="scan-import-manual-entry"
            className="border-t border-border px-3 py-3 flex flex-col gap-2 sm:flex-row"
            onSubmit={handleManualSubmit}
          >
            <label className="sr-only" htmlFor="scan-import-manual-code">
              Bag code
            </label>
            <input
              id="scan-import-manual-code"
              type="text"
              className="input min-w-0 flex-1"
              value={manualCode}
              onChange={e => setManualCode(e.target.value)}
              placeholder="Paste bag code"
              required
            />
            <button
              type="submit"
              className="btn-primary btn-sm inline-flex items-center justify-center gap-1"
              disabled={manualSubmitting || manualCode.trim().length === 0}
            >
              <Plus size={14} />
              Add bag
            </button>
          </form>
        )}
      </div>
      <Scanner
        onScan={handleScan}
        symbologies={SCAN_IMPORT_SYMBOLOGIES}
        className="flex flex-col h-[55vh]"
      />
      <div className="mt-3 text-xs text-muted leading-relaxed">
        Aim at the <strong className="text-text">2D Data Matrix</strong>{" "}
        (the small square code) on a Mouser or DigiKey bag — that's the
        one that carries the full part record. The plain 1D barcodes
        alongside it only contain individual fields and won't resolve
        to a part. Duplicates already in your library are flagged
        automatically.
      </div>
    </div>
  );
}
