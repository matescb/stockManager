import { useCallback, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  ImageOff,
  Link2,
  Loader2,
  Package,
  Trash2,
} from "lucide-react";
import Scanner, { ScanResult } from "@/components/scanner/Scanner";
import { api, ApiError } from "@/lib/api";
import { parseBagCode, bagLotName, bagComments, type BagCode } from "@/lib/bagCode";
import type {
  MpnLookupResult,
  Part,
  StorageLocation,
} from "@/types";

const PROVIDER_LABEL: Record<string, string> = {
  mouser: "Mouser",
  digikey: "DigiKey",
  none: "no provider",
};

type LookupState =
  | { kind: "pending" }
  | { kind: "duplicate"; existing: Part }
  | { kind: "found"; result: NonNullable<MpnLookupResult["result"]>; provider: string }
  | { kind: "error"; message: string };

type Row = {
  rowId: string;     // local id for rendering / dedup
  bag: BagCode;      // every field the parser pulled off the bag
  quantity: number;  // editable, defaults to bag's Q if any
  state: LookupState;
};

/** Loose, case-insensitive comparison so "Molex" matches "MOLEX INC."
 *  and "Yageo" matches "YAGEO Phycomp". Returns true when the two
 *  strings refer to the same manufacturer. */
function manufacturerMatches(bagMfr: string | undefined, providerMfr: string | null | undefined): boolean {
  if (!bagMfr || !providerMfr) return true;  // can't disagree if one is missing
  const a = bagMfr.toLowerCase().replace(/\s+/g, " ").trim();
  const b = providerMfr.toLowerCase().replace(/\s+/g, " ").trim();
  return a.includes(b) || b.includes(a);
}

type ImportResultRow = {
  mpn: string;
  status: "created" | "duplicate" | "lookup_failed" | "invalid";
  part_id?: string;
  quantity_added?: number;
  stock_error?: string | null;
  error?: string;
};

type ImportResponse = {
  rows: ImportResultRow[];
  summary: { created: number; duplicate: number; lookup_failed: number; invalid: number };
  provider: string;
};

function newRowId(): string {
  // crypto.randomUUID would also work but isn't guaranteed in older TLS hosts.
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

export default function ScanImport() {
  const nav = useNavigate();
  const [rows, setRows] = useState<Row[]>([]);
  const [storageId, setStorageId] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [lastSummary, setLastSummary] = useState<ImportResponse | null>(null);

  // Dedup against rapid re-scan of the same bag — Scandit fires didScan
  // continuously while the code is in frame. We key by parsed MPN.
  const seenMpns = useRef<Set<string>>(new Set());

  const { data: storages } = useQuery({
    queryKey: ["storage-locations"],
    queryFn: () => api.get<StorageLocation[]>("/storage"),
  });

  const handleScan = useCallback(async (s: ScanResult) => {
    const parsed = parseBagCode(s.data);
    // Diagnostics — when the parser misreads a real-world bag code, the
    // raw byte stream from the scanner is the ground truth. Logging the
    // codepoints (not just the rendered string) is what makes munged
    // separators visible in DevTools.
    // eslint-disable-next-line no-console
    console.log("[bag scan]", {
      symbology: s.symbology,
      raw: s.data,
      escaped: JSON.stringify(s.data),
      length: s.data.length,
      codepoints: Array.from(s.data, c => c.charCodeAt(0)),
      parsed,
    });
    const mpn = parsed.mpn.trim();
    if (!mpn) return;
    if (seenMpns.current.has(mpn)) return;
    seenMpns.current.add(mpn);

    const rowId = newRowId();
    const initialQty = parsed.quantity ?? 0;
    setRows(prev => [
      ...prev,
      { rowId, bag: parsed, quantity: initialQty, state: { kind: "pending" } },
    ]);

    // Run duplicate check + provider lookup concurrently — they hit
    // different paths and either may resolve first. The provider call
    // is the slow one (Mouser/DigiKey ~1-2s), the dup check is local.
    const setState = (state: LookupState) =>
      setRows(prev => prev.map(r => (r.rowId === rowId ? { ...r, state } : r)));

    try {
      const dupes = await api.get<Part[]>(`/parts?mpn=${encodeURIComponent(mpn)}`);
      if (dupes.length > 0) {
        setState({ kind: "duplicate", existing: dupes[0] });
        return;
      }
      const lookup = await api.post<MpnLookupResult>("/parts/lookup-mpn", { mpn });
      if (lookup.found && lookup.result) {
        setState({ kind: "found", result: lookup.result, provider: lookup.provider });
      } else {
        setState({ kind: "error", message: lookup.message || "no match" });
      }
    } catch (e) {
      setState({ kind: "error", message: e instanceof ApiError ? e.message : "Lookup failed" });
    }
  }, []);

  const importable = useMemo(
    () => rows.filter(r => r.state.kind === "found"),
    [rows]
  );

  function removeRow(rowId: string) {
    setRows(prev => {
      const dropped = prev.find(r => r.rowId === rowId);
      if (dropped) seenMpns.current.delete(dropped.bag.mpn);
      return prev.filter(r => r.rowId !== rowId);
    });
  }

  function setQuantity(rowId: string, qty: number) {
    setRows(prev =>
      prev.map(r => (r.rowId === rowId ? { ...r, quantity: Math.max(0, qty | 0) } : r))
    );
  }

  async function submitAll() {
    if (importable.length === 0) {
      toast.error("Nothing to import.");
      return;
    }
    setSubmitting(true);
    try {
      const out = await api.post<ImportResponse>("/parts/bulk-import-from-scan", {
        rows: importable.map(r => ({
          mpn: r.bag.mpn,
          quantity: r.quantity > 0 ? r.quantity : undefined,
          storage_location_id: storageId || undefined,
          lot_name: bagLotName(r.bag) ?? undefined,
          lot_serial: r.bag.serial,
          comments: bagComments(r.bag) ?? undefined,
        })),
      });
      setLastSummary(out);
      // Drop rows that were just imported successfully so the operator
      // can keep scanning new bags without losing duplicate / errored rows.
      const importedMpns = new Set(
        out.rows.filter(r => r.status === "created").map(r => r.mpn)
      );
      setRows(prev => prev.filter(r => !importedMpns.has(r.bag.mpn)));
      importedMpns.forEach(m => seenMpns.current.delete(m));
      toast.success(
        `Imported ${out.summary.created} part${out.summary.created === 1 ? "" : "s"}.`
      );
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Import failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h1 className="text-xl font-semibold">Scan to import</h1>
        <Link to="/parts" className="btn">Back to parts</Link>
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        {/* Camera column */}
        <div className="card p-3">
          <Scanner
            onScan={handleScan}
            symbologies={["DataMatrix", "QR"]}
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

        {/* List + import column */}
        <div className="card p-3 flex flex-col">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-md font-semibold">
              Scanned ({rows.length})
            </h2>
            <button
              type="button"
              className="btn-primary"
              disabled={submitting || importable.length === 0}
              onClick={submitAll}
            >
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Import {importable.length > 0 ? `(${importable.length})` : ""}
            </button>
          </div>

          <div className="mb-3 text-xs text-muted">
            <label className="block mb-1">Initial stock location (optional)</label>
            <select
              className="input w-full"
              value={storageId}
              onChange={e => setStorageId(e.target.value)}
            >
              <option value="">— file later (no location) —</option>
              {(storages ?? []).filter(s => !s.archived_at).map(s => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
            <div className="mt-1">
              Each row's quantity (from the bag's Q field, or your edits)
              lands on-hand at import. Pick a location to file it directly,
              or leave blank to record it without a location and move it
              later from the Stock view.
            </div>
          </div>

          {lastSummary && (
            <div className="mb-3 rounded-md border border-border bg-panel2/50 p-2 text-xs">
              Last import:{" "}
              <span className="text-accent">{lastSummary.summary.created} created</span>
              {lastSummary.summary.duplicate > 0 && (
                <>, <span className="text-warning">{lastSummary.summary.duplicate} duplicate</span></>
              )}
              {lastSummary.summary.lookup_failed > 0 && (
                <>, <span className="text-danger">{lastSummary.summary.lookup_failed} not found</span></>
              )}
              .
            </div>
          )}

          <div className="overflow-y-auto flex-1 -mx-3 px-3 space-y-2">
            {rows.length === 0 && (
              <div className="text-sm text-muted py-6 text-center">
                No scans yet — point the camera at a bag's 2D code.
              </div>
            )}
            {rows.map(r => (
              <ScanCard
                key={r.rowId}
                row={r}
                onRemove={() => removeRow(r.rowId)}
                onQuantity={q => setQuantity(r.rowId, q)}
                onOpenExisting={() => {
                  if (r.state.kind === "duplicate") nav(`/parts/${r.state.existing.id}/info`);
                }}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function ScanCard({
  row,
  onRemove,
  onQuantity,
  onOpenExisting,
}: {
  row: Row;
  onRemove: () => void;
  onQuantity: (q: number) => void;
  onOpenExisting: () => void;
}) {
  return (
    <div className="rounded-md border border-border bg-panel2/40 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="font-mono text-sm">{row.bag.mpn}</div>
        <button
          type="button"
          className="btn-ghost btn-sm"
          onClick={onRemove}
          aria-label={`Remove ${row.bag.mpn}`}
        >
          <Trash2 size={14} className="text-danger" />
        </button>
      </div>
      <div className="mt-2">
        {row.state.kind === "pending" && (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 className="h-4 w-4 animate-spin" /> Looking up…
          </div>
        )}
        {row.state.kind === "duplicate" && (
          <div className="flex items-start gap-2 text-sm">
            <AlertTriangle className="h-4 w-4 text-warning shrink-0 mt-0.5" />
            <div>
              <div>Already in library: <span className="font-medium">{row.state.existing.name}</span></div>
              <button
                type="button"
                className="text-accent hover:underline text-xs mt-1 inline-flex items-center gap-1"
                onClick={onOpenExisting}
              >
                <Link2 size={12} /> Open existing part
              </button>
            </div>
          </div>
        )}
        {row.state.kind === "error" && (
          <div className="flex items-start gap-2 text-sm">
            <AlertTriangle className="h-4 w-4 text-danger shrink-0 mt-0.5" />
            <div className="text-danger">{row.state.message}</div>
          </div>
        )}
        {row.state.kind === "found" && (
          <FoundDetails
            state={row.state}
            bag={row.bag}
            qty={row.quantity}
            onQuantity={onQuantity}
          />
        )}
      </div>
    </div>
  );
}

function FoundDetails({
  state,
  bag,
  qty,
  onQuantity,
}: {
  state: Extract<LookupState, { kind: "found" }>;
  bag: BagCode;
  qty: number;
  onQuantity: (q: number) => void;
}) {
  const r = state.result;
  const mfrMismatch = !manufacturerMatches(bag.manufacturer, r.manufacturer);
  const lotName = bagLotName(bag);
  const comments = bagComments(bag);
  return (
    <div>
      <div className="flex items-start gap-2">
        <CheckCircle2 className="h-4 w-4 text-accent shrink-0 mt-0.5" />
        <div className="flex-1">
          <div className="text-sm">
            <span className="font-medium">{r.manufacturer ?? "—"}</span>
            <span className="text-muted ml-2 text-xs">
              {PROVIDER_LABEL[state.provider] ?? state.provider}
            </span>
          </div>
          {mfrMismatch && (
            <div className="mt-1 text-xs text-warning inline-flex items-start gap-1">
              <AlertTriangle size={12} className="shrink-0 mt-0.5" />
              <span>
                Bag says <span className="font-medium">{bag.manufacturer}</span> —
                provider returned <span className="font-medium">{r.manufacturer}</span>.
                Double-check this is the right part before importing.
              </span>
            </div>
          )}
          {r.description && (
            <div className="text-xs text-muted mt-1">{r.description}</div>
          )}
          <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2 text-xs">
            {r.footprint && (
              <span className="inline-flex items-center gap-1">
                <Package size={12} /> {r.footprint}
              </span>
            )}
            {r.category && <span className="text-muted">{r.category}</span>}
            {r.source_url && (
              <a
                className="text-accent hover:underline inline-flex items-center gap-1"
                href={r.source_url}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink size={12} /> Product page
              </a>
            )}
          </div>
        </div>
        {r.image_url ? (
          <img
            src={r.image_url}
            alt=""
            className="w-12 h-12 rounded border border-border object-contain bg-white"
            onError={e => { (e.target as HTMLImageElement).style.display = "none"; }}
          />
        ) : (
          <div className="w-12 h-12 rounded border border-border flex items-center justify-center text-muted">
            <ImageOff size={16} />
          </div>
        )}
      </div>

      {/* Traceability — anything the bag carried in MIL-STD-130N fields.
          Saved verbatim onto the lot/stock-entry at import time. */}
      {(lotName || comments || bag.serial) && (
        <div className="mt-2 rounded border border-border/60 bg-panel/40 px-2 py-1.5 text-xs">
          <div className="text-muted uppercase tracking-wider text-[10px] mb-0.5">
            Traceability
          </div>
          {lotName && <div>{lotName}</div>}
          {bag.serial && <div>Serial: <span className="font-mono">{bag.serial}</span></div>}
          {comments && <div className="text-muted">{comments}</div>}
        </div>
      )}

      {/* Top spec rows (capped) — gives a glance at parametric data */}
      {r.specs.length > 0 && (
        <details className="mt-2">
          <summary className="text-xs text-muted cursor-pointer">
            {r.specs.length} spec{r.specs.length === 1 ? "" : "s"}
          </summary>
          <div className="mt-1 grid grid-cols-[max-content_1fr] gap-x-2 gap-y-0.5 text-xs">
            {r.specs.slice(0, 12).map(s => (
              <div key={s.key} className="contents">
                <div className="text-muted">{s.key}</div>
                <div className="font-mono break-all">{s.value}</div>
              </div>
            ))}
            {r.specs.length > 12 && (
              <div className="col-span-2 text-muted italic">
                …and {r.specs.length - 12} more (visible after import)
              </div>
            )}
          </div>
        </details>
      )}

      <div className="mt-3 flex items-center gap-2">
        <label className="text-xs text-muted">Qty</label>
        <input
          type="number"
          min={0}
          className="input w-24 text-sm"
          value={qty}
          onChange={e => onQuantity(parseInt(e.target.value, 10) || 0)}
        />
        <span className="text-xs text-muted">
          {qty === 0
            ? "no initial stock entry"
            : `lands ${qty} on-hand at import`}
        </span>
      </div>
    </div>
  );
}
