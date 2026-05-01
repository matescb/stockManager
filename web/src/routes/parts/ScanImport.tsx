import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  ImageOff,
  Link2,
  Loader2,
  Minus,
  Package,
  Plus,
  RotateCcw,
  Trash2,
} from "lucide-react";
import Scanner, { ScanResult } from "@/components/scanner/Scanner";
import { api, ApiError } from "@/lib/api";
import { parseBagCode, bagLotName, bagComments, bagSignature, type BagCode } from "@/lib/bagCode";
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

// Module-level constant — referenced by `<Scanner symbologies={...}>` below.
// Inline `["DataMatrix", "QR"]` would be a fresh array on every render, which
// (combined with effect deps in ScanditScanner / ZxingScanner) would tear
// down and rebuild the multi-MB scanner SDK on every parent state change.
// FE CRIT-2 in the 2026-04-30 review.
const SCAN_IMPORT_SYMBOLOGIES = ["DataMatrix", "QR"] as const;

type LookupState =
  | { kind: "pending" }
  | { kind: "duplicate"; existing: Part }
  | { kind: "found"; result: NonNullable<MpnLookupResult["result"]>; provider: string }
  // Same physical bag was imported earlier — surface the prior coordinates
  // so the user can either open the part or consume from the lot inline.
  | {
      kind: "bag_rescan";
      part_id: string;
      lot_id: string | null;
      storage_location_id: string | null;
      quantity: number;
    }
  | { kind: "consumed"; partId: string; quantity: number }
  | { kind: "error"; message: string };

type Row = {
  rowId: string;     // local id for rendering / dedup
  bag: BagCode;      // every field the parser pulled off the bag
  bagSig: string | null;  // sha256 of the raw bag — null when no Web Crypto / empty
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
  status: "created" | "duplicate" | "bag_rescan" | "lookup_failed" | "invalid";
  part_id?: string;
  quantity_added?: number;
  stock_error?: string | null;
  error?: string;
};

type ImportResponse = {
  rows: ImportResultRow[];
  summary: {
    created: number;
    duplicate: number;
    bag_rescan: number;
    lookup_failed: number;
    invalid: number;
  };
  provider: string;
};

function newRowId(): string {
  // crypto.randomUUID would also work but isn't guaranteed in older TLS hosts.
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

// "Service Unavailable", "upstream unavailable (TimeoutException)",
// "DigiKey rate limit reached", connection-resets — anything that's
// likely to clear on its own. Detected so we can retry transparently
// instead of dumping the user back to "Service Unavailable" under the
// MPN.
function isTransientLookupFailure(err: unknown): boolean {
  if (err instanceof ApiError) {
    if (err.status >= 500) return true;
    const msg = (err.message || "").toLowerCase();
    return /unavailable|timeout|rate.?limit|temporar|503|504|502|connection/.test(msg);
  }
  if (err instanceof TypeError) {
    // Browser fetch throws TypeError on connection abort / network change.
    return true;
  }
  return false;
}

function isTransientResultMessage(msg: string | undefined | null): boolean {
  if (!msg) return false;
  return /unavailable|timeout|rate.?limit|temporar|service.?unavailable/i.test(msg);
}

const RETRY_DELAYS_MS = [800, 1600, 3000];

async function lookupMpnWithRetry(mpn: string): Promise<MpnLookupResult> {
  // First attempt + up to len(RETRY_DELAYS_MS) retries. We retry both on
  // throws (5xx, network) and on 200-with-transient-message (the provider
  // route swallows upstream failures and returns {found:false,message}).
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
  // Exhausted retries on a transient — final attempt's result is what
  // the operator gets to see. We never reach here because the loop
  // either returns or throws on every iteration; TS just can't tell.
  /* istanbul ignore next */
  throw new Error("lookupMpnWithRetry: unreachable");
}

export default function ScanImport() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [searchParams] = useSearchParams();
  const [rows, setRows] = useState<Row[]>([]);
  // Pre-select the storage when arriving via /storage/<id> "Scan into here"
  // — the destination is whichever bin the user opened.
  const [storageId, setStorageId] = useState<string>(() => searchParams.get("storage_id") ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [lastSummary, setLastSummary] = useState<ImportResponse | null>(null);

  // Dedup against the camera firing didScan continuously while a code is
  // in frame: we key by signature first (catches the same bag re-read from
  // a slightly different angle), and fall back to MPN for non-2D scans.
  const seenSigs = useRef<Set<string>>(new Set());
  const seenMpns = useRef<Set<string>>(new Set());

  // Honour ?storage_id= even when the URL changes mid-session (e.g. a
  // back-button navigation drops us back here from Storage).
  useEffect(() => {
    const fromUrl = searchParams.get("storage_id");
    if (fromUrl && fromUrl !== storageId) setStorageId(fromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const { data: storages } = useQuery({
    queryKey: ["storage-locations"],
    queryFn: () => api.get<StorageLocation[]>("/storage"),
  });

  const handleScan = useCallback(async (s: ScanResult) => {
    const parsed = parseBagCode(s.data);
    const sig = await bagSignature(s.data);
    const mpn = parsed.mpn.trim();
    if (!mpn) return;
    // Dedup by signature first (the same physical bag re-fires the scan
    // event while it's in frame), then by MPN as a fallback for 1D codes
    // and crypto-less browsers.
    if (sig && seenSigs.current.has(sig)) return;
    if (!sig && seenMpns.current.has(mpn)) return;
    if (sig) seenSigs.current.add(sig);
    seenMpns.current.add(mpn);

    const rowId = newRowId();
    const initialQty = parsed.quantity ?? 0;
    setRows(prev => [
      ...prev,
      { rowId, bag: parsed, bagSig: sig, quantity: initialQty, state: { kind: "pending" } },
    ]);

    const setState = (state: LookupState) =>
      setRows(prev => prev.map(r => (r.rowId === rowId ? { ...r, state } : r)));

    try {
      // 1. Bag-rescan check — only meaningful when we have a signature,
      //    since the lookup is signature-based.
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

      // 2. Same-MPN duplicate (different bag, same part).
      const dupes = await api.get<Part[]>(`/parts?mpn=${encodeURIComponent(mpn)}`);
      if (dupes.length > 0) {
        setState({ kind: "duplicate", existing: dupes[0] });
        return;
      }

      // 3. New part — provider lookup with auto-retry on transient
      //    upstream failures (5xx, "Service Unavailable", timeouts).
      const lookup = await lookupMpnWithRetry(mpn);
      if (lookup.found && lookup.result) {
        setState({ kind: "found", result: lookup.result, provider: lookup.provider });
      } else {
        setState({ kind: "error", message: lookup.message || "no match" });
      }
    } catch (e) {
      setState({ kind: "error", message: e instanceof ApiError ? e.message : "Lookup failed" });
    }
  }, []);

  async function quickRemoveFromBag(rowId: string, quantity: number) {
    const row = rows.find(r => r.rowId === rowId);
    if (!row || row.state.kind !== "bag_rescan") return;
    const st = row.state;
    if (quantity <= 0 || quantity > st.quantity) return;
    try {
      await api.post(`/parts/${st.part_id}/quick-remove-bag`, {
        quantity,
        lot_id: st.lot_id,
        storage_location_id: st.storage_location_id,
      });
      setRows(prev =>
        prev.map(r =>
          r.rowId === rowId
            ? { ...r, state: { kind: "consumed", partId: st.part_id, quantity } }
            : r,
        ),
      );
      qc.invalidateQueries({ queryKey: ["part", st.part_id] });
      qc.invalidateQueries({ queryKey: ["part", st.part_id, "stock"] });
      toast.success(`Removed ${quantity} from this bag.`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Quick-remove failed");
    }
  }

  const importable = useMemo(
    () => rows.filter(r => r.state.kind === "found"),
    [rows]
  );

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
          bag_signature: r.bagSig ?? undefined,
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
                  else if (r.state.kind === "bag_rescan") nav(`/parts/${r.state.part_id}/info`);
                  else if (r.state.kind === "consumed") nav(`/parts/${r.state.partId}/info`);
                }}
                onQuickRemove={(qty) => quickRemoveFromBag(r.rowId, qty)}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function BagRescanCard({
  quantity,
  onOpenExisting,
  onQuickRemove,
}: {
  /** Total qty in the recognised bag — caps the stepper. */
  quantity: number;
  onOpenExisting: () => void;
  onQuickRemove: (qty: number) => void;
}) {
  // Default to 1 — most "I'm taking some out of this bag" actions are
  // for a single unit. Operator can step up via the +/- buttons or
  // type a number directly. Capped at the bag's available qty.
  const [qty, setQty] = useState(1);
  const cap = Math.max(1, quantity);
  function step(delta: number) {
    setQty(q => Math.min(cap, Math.max(1, q + delta)));
  }
  return (
    <div className="flex items-start gap-2 text-sm">
      <RotateCcw className="h-4 w-4 text-accent shrink-0 mt-0.5" />
      <div className="flex-1">
        <div>
          Recognised — this bag was imported earlier
          {quantity > 0 && (
            <> (bag had qty <span className="font-mono">{quantity}</span>)</>
          )}
          .
        </div>
        <div className="flex flex-wrap items-center gap-2 mt-2">
          <button
            type="button"
            className="btn-ghost btn-sm inline-flex items-center gap-1"
            onClick={onOpenExisting}
          >
            <Link2 size={12} /> Open part
          </button>
          <div className="flex items-center gap-1 ml-auto">
            <button
              type="button"
              className="btn-ghost btn-sm h-8 w-8 inline-flex items-center justify-center"
              onClick={() => step(-1)}
              disabled={qty <= 1}
              aria-label="Decrease quantity"
            >
              <Minus size={14} />
            </button>
            <input
              type="number"
              className="input w-16 text-center"
              min={1}
              max={cap}
              value={qty}
              onChange={e => setQty(Math.max(1, Math.min(cap, parseInt(e.target.value, 10) || 1)))}
            />
            <button
              type="button"
              className="btn-ghost btn-sm h-8 w-8 inline-flex items-center justify-center"
              onClick={() => step(1)}
              disabled={qty >= cap}
              aria-label="Increase quantity"
            >
              <Plus size={14} />
            </button>
          </div>
          <button
            type="button"
            className="btn-primary btn-sm inline-flex items-center gap-1"
            onClick={() => onQuickRemove(qty)}
            disabled={cap <= 0 || qty <= 0 || qty > cap}
            title={`Remove ${qty} unit${qty === 1 ? "" : "s"} from this lot`}
          >
            <Minus size={12} /> Remove {qty}
          </button>
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
  onQuickRemove,
}: {
  row: Row;
  onRemove: () => void;
  onQuantity: (q: number) => void;
  onOpenExisting: () => void;
  onQuickRemove: (qty: number) => void;
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
        {row.state.kind === "bag_rescan" && (
          <BagRescanCard
            quantity={row.state.quantity}
            onOpenExisting={onOpenExisting}
            onQuickRemove={onQuickRemove}
          />
        )}
        {row.state.kind === "consumed" && (
          <div className="flex items-start gap-2 text-sm text-success">
            <CheckCircle2 className="h-4 w-4 shrink-0 mt-0.5" />
            <div>
              Removed {row.state.quantity} from this bag.{" "}
              <button
                type="button"
                className="text-accent hover:underline text-xs ml-1"
                onClick={onOpenExisting}
              >
                Open part
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
