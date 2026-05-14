/**
 * ScanImportQueue — the scrollable list of scanned rows.
 *
 * Renders each row as a ScanCard; inline helpers BagRescanCard and
 * FoundDetails keep the visual logic co-located without exporting them.
 *
 * Props:
 *  rows          — current scan queue (from parent / useScanImportRows)
 *  onRemove      — remove a row by rowId
 *  onQuantity    — update a row's qty by rowId
 *  onOpenExisting — navigate to an existing part (duplicate / bag_rescan / consumed)
 *  onQuickRemove — quick-remove N units from a bag_rescan row
 */
import { useState } from "react";
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
import { bagLotName, bagComments, type BagCode } from "@/lib/bagCode";
import { PROVIDER_LABEL, manufacturerMatches, type LookupState, type Row } from "./types";

// ─── BagRescanCard ────────────────────────────────────────────────────────────

function BagRescanCard({
  quantity,
  onOpenExisting,
  onQuickRemove,
}: {
  quantity: number;
  onOpenExisting: () => void;
  onQuickRemove: (qty: number) => void;
}) {
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
              onChange={e =>
                setQty(Math.max(1, Math.min(cap, parseInt(e.target.value, 10) || 1)))
              }
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

// ─── FoundDetails ─────────────────────────────────────────────────────────────

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
                rel="noopener noreferrer"
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
            onError={e => {
              (e.target as HTMLImageElement).style.display = "none";
            }}
          />
        ) : (
          <div className="w-12 h-12 rounded border border-border flex items-center justify-center text-muted">
            <ImageOff size={16} />
          </div>
        )}
      </div>

      {/* Traceability — anything the bag carried in MIL-STD-130N fields. */}
      {(lotName || comments || bag.serial) && (
        <div className="mt-2 rounded border border-border/60 bg-panel/40 px-2 py-1.5 text-xs">
          <div className="text-muted uppercase tracking-wider text-[10px] mb-0.5">
            Traceability
          </div>
          {lotName && <div>{lotName}</div>}
          {bag.serial && (
            <div>
              Serial: <span className="font-mono">{bag.serial}</span>
            </div>
          )}
          {comments && <div className="text-muted">{comments}</div>}
        </div>
      )}

      {/* Top spec rows (capped) */}
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
          {qty === 0 ? "no initial stock entry" : `lands ${qty} on-hand at import`}
        </span>
      </div>
    </div>
  );
}

// ─── ScanCard ─────────────────────────────────────────────────────────────────

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
              <div>
                Already in library:{" "}
                <span className="font-medium">{row.state.existing.name}</span>
              </div>
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

// ─── ScanImportQueue (exported) ───────────────────────────────────────────────

interface ScanImportQueueProps {
  rows: Row[];
  onRemove: (rowId: string) => void;
  onQuantity: (rowId: string, qty: number) => void;
  onOpenExisting: (row: Row) => void;
  onQuickRemove: (rowId: string, qty: number) => void;
}

export default function ScanImportQueue({
  rows,
  onRemove,
  onQuantity,
  onOpenExisting,
  onQuickRemove,
}: ScanImportQueueProps) {
  return (
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
          onRemove={() => onRemove(r.rowId)}
          onQuantity={q => onQuantity(r.rowId, q)}
          onOpenExisting={() => onOpenExisting(r)}
          onQuickRemove={qty => onQuickRemove(r.rowId, qty)}
        />
      ))}
    </div>
  );
}
