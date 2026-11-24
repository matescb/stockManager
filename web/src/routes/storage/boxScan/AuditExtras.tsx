/** Bags scanned during an audit that are not recorded in this box. */
import { ArrowRightLeft, Loader2, Plus, Trash2 } from "lucide-react";
import type { ResolveResult } from "./api";
import MpnEntry from "./MpnEntry";

export type ExtraBag = {
  id: string;
  mpn: string;
  quantity: number;
  sig: string | null;
  raw: string;
  lotName: string | null;
  lotSerial: string | null;
  comments: string | null;
  /** The label had no 1P and matched no recorded bag: type the MPN. */
  needsMpn?: boolean;
  noMpnMessage?: string;
  resolve?: ResolveResult;
  busy: boolean;
  error?: string;
};

type Props = {
  extras: ExtraBag[];
  onQuantity: (id: string, q: number) => void;
  onAdd: (extra: ExtraBag) => void;
  onMpn: (extra: ExtraBag, mpn: string) => void;
  onDismiss: (id: string) => void;
};

function describe(e: ExtraBag): string {
  const r = e.resolve;
  if (!r) return "Looking up…";
  if (e.needsMpn) return e.noMpnMessage ?? "This label has no manufacturer part number — type the MPN.";
  if (r.status === "bag_elsewhere") {
    const where = r.bag?.locations.map(l => `${l.name ?? "no location"} (${l.quantity})`).join(", ");
    return `Recorded in ${where}`;
  }
  if (r.status === "existing_part") return "Part is in the library, this bag is not recorded";
  return "Not in the library — adding creates the part from the provider";
}

export default function AuditExtras({ extras, onQuantity, onAdd, onMpn, onDismiss }: Props) {
  if (extras.length === 0) return null;
  return (
    <section className="space-y-2">
      <h3 className="section-title">Found here, not recorded in this box ({extras.length})</h3>
      {extras.map(e => (
        <div key={e.id} className="card p-3 flex flex-col gap-2 sm:flex-row sm:items-center border-warning/50">
          <div className="min-w-0 flex-1">
            <div className="font-medium truncate">{e.resolve?.part?.name ?? (e.mpn || "No part number on label")}</div>
            <div className="text-xs text-muted">{describe(e)}{e.lotName ? ` · ${e.lotName}` : ""}</div>
            {e.error && <div className="text-xs text-danger">{e.error}</div>}
            {e.needsMpn && <MpnEntry id={e.id} disabled={e.busy} onSubmit={mpn => onMpn(e, mpn)} />}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <label className="sr-only" htmlFor={`extra-qty-${e.id}`}>Quantity</label>
            <input
              id={`extra-qty-${e.id}`}
              type="number"
              min={1}
              step={1}
              className="input w-24 tabular-nums"
              value={e.quantity || ""}
              disabled={e.busy}
              onChange={ev => onQuantity(e.id, Math.max(0, Math.floor(Number(ev.target.value) || 0)))}
            />
            <button
              type="button"
              className="btn-primary btn-sm inline-flex items-center gap-1"
              disabled={e.busy || !e.resolve || e.needsMpn || !(e.quantity > 0)}
              onClick={() => onAdd(e)}
            >
              {e.busy ? <Loader2 size={14} className="animate-spin" /> : e.resolve?.status === "bag_elsewhere" ? <ArrowRightLeft size={14} /> : <Plus size={14} />}
              {e.resolve?.status === "bag_elsewhere" ? "Move here" : "Add to box"}
            </button>
            <button type="button" className="btn btn-sm" aria-label="Ignore" title="Ignore" disabled={e.busy} onClick={() => onDismiss(e.id)}>
              <Trash2 size={14} />
            </button>
          </div>
        </div>
      ))}
    </section>
  );
}
