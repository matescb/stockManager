/**
 * ScanImportActions — the submit/control panel for the scan queue.
 *
 * Renders:
 *  - Import button (disabled when nothing importable or submitting)
 *  - Storage location selector
 *  - Last-import summary card
 *
 * Purely presentational — all async logic lives in the parent (ScanImport).
 */
import { Loader2 } from "lucide-react";
import type { StorageLocation } from "@/types";
import type { ImportResponse } from "./types";

interface ScanImportActionsProps {
  rowCount: number;
  importableCount: number;
  submitting: boolean;
  storageId: string;
  storages: StorageLocation[] | undefined;
  lastSummary: ImportResponse | null;
  onStorageChange: (id: string) => void;
  onSubmit: () => void;
}

export default function ScanImportActions({
  rowCount,
  importableCount,
  submitting,
  storageId,
  storages,
  lastSummary,
  onStorageChange,
  onSubmit,
}: ScanImportActionsProps) {
  return (
    <>
      {/* Header row: count + import button */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="card-title">Scanned ({rowCount})</h2>
        <button
          type="button"
          className="btn-primary"
          disabled={submitting || importableCount === 0}
          onClick={onSubmit}
        >
          {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          Import {importableCount > 0 ? `(${importableCount})` : ""}
        </button>
      </div>

      {/* Storage location selector */}
      <div className="mb-3 text-xs text-muted">
        <label className="block mb-1">Initial stock location (optional)</label>
        <select
          className="input w-full"
          value={storageId}
          onChange={e => onStorageChange(e.target.value)}
        >
          <option value="">— file later (no location) —</option>
          {(storages ?? [])
            .filter(s => !s.archived_at)
            .map(s => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
        </select>
        <div className="mt-1">
          Each row's quantity (from the bag's Q field, or your edits) lands
          on-hand at import. Pick a location to file it directly, or leave
          blank to record it without a location and move it later from the
          Stock view.
        </div>
      </div>

      {/* Last-import summary card */}
      {lastSummary && (
        <div className="mb-3 rounded-md border border-border bg-panel2/50 p-2 text-xs">
          Last import:{" "}
          <span className="text-accent">{lastSummary.summary.created} created</span>
          {lastSummary.summary.duplicate > 0 && (
            <>
              ,{" "}
              <span className="text-warning">
                {lastSummary.summary.duplicate} duplicate
              </span>
            </>
          )}
          {lastSummary.summary.lookup_failed > 0 && (
            <>
              ,{" "}
              <span className="text-danger">
                {lastSummary.summary.lookup_failed} not found
              </span>
            </>
          )}
          {(lastSummary.summary.needs_disambiguation ?? 0) > 0 && (
            <>
              ,{" "}
              <span className="text-warning">
                {lastSummary.summary.needs_disambiguation} ambiguous match
              </span>
            </>
          )}
          .
        </div>
      )}
    </>
  );
}
