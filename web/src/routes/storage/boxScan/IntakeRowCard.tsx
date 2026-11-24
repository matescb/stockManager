import { Link } from "react-router-dom";
import { ArrowRightLeft, Check, Loader2, Plus, Trash2, Undo2 } from "lucide-react";
import type { IntakeRow } from "./intakeRows";
import MpnEntry from "./MpnEntry";

type Props = {
  row: IntakeRow;
  highlighted: boolean;
  onQuantity: (q: number) => void;
  onMpn: (mpn: string) => void;
  onSave: (onExisting?: "move" | "add_new") => void;
  onUpdateCount: () => void;
  onUndo: () => void;
  onDismiss: () => void;
};

function title(row: IntakeRow): { name: string; sub: string | null; isNew: boolean } {
  const part = row.result?.part ?? row.resolve?.part;
  if (part) return { name: part.name, sub: [part.mpn, part.manufacturer].filter(Boolean).join(" · "), isNew: !!row.result?.part_created };
  const prov = row.resolve?.provider;
  if (prov) return { name: prov.mpn, sub: [prov.manufacturer, prov.description].filter(Boolean).join(" · "), isNew: true };
  if (!row.mpn) return { name: "No part number on label", sub: row.comments ?? null, isNew: false };
  return { name: row.mpn, sub: null, isNew: false };
}

function statusText(row: IntakeRow): { text: string; tone: string } {
  switch (row.phase) {
    case "resolving": return { text: "Looking up…", tone: "text-muted" };
    case "saving": return { text: "Saving…", tone: "text-muted" };
    case "error": return { text: row.error ?? "Failed", tone: "text-danger" };
    case "needs_mpn": return { text: row.noMpnMessage ?? "This label has no manufacturer part number — type the MPN.", tone: "text-warning" };
    case "needs_choice": return { text: "Recorded in another box", tone: "text-warning" };
    case "ready":
      return row.quantity > 0
        ? { text: "Not saved yet", tone: "text-warning" }
        : { text: "Enter the quantity, then Add", tone: "text-warning" };
    case "saved":
      if (row.result?.status === "moved") return { text: "Moved here", tone: "text-success" };
      if (row.result?.status === "already_here") return { text: "Already in this box", tone: "text-success" };
      return { text: "Added", tone: "text-success" };
  }
}

export default function IntakeRowCard({ row, highlighted, onQuantity, onMpn, onSave, onUpdateCount, onUndo, onDismiss }: Props) {
  const t = title(row);
  const s = statusText(row);
  const busy = row.phase === "resolving" || row.phase === "saving";
  const dirty = row.phase === "saved" && row.savedQuantity !== row.quantity;
  const elsewhere = row.resolve?.bag?.locations ?? [];

  return (
    <div data-testid="intake-row" className={`card p-3 flex flex-col gap-2 sm:flex-row sm:items-center ${highlighted ? "ring-2 ring-accent" : ""}`}>
      <div className="min-w-0 flex-1">
        <div className="font-medium truncate">
          {row.result?.part ? <Link to={`/parts/${row.result.part.id}/stock`} className="hover:underline">{t.name}</Link> : t.name}
          {t.isNew && <span className="pill ml-2">new part</span>}
        </div>
        {t.sub && <div className="text-xs text-muted truncate">{t.sub}</div>}
        <div className="text-xs mt-0.5 flex flex-wrap gap-x-3">
          <span className={`inline-flex items-center gap-1 ${s.tone}`}>
            {busy && <Loader2 size={12} className="animate-spin" />}
            {row.phase === "saved" && <Check size={12} />}
            {s.text}
          </span>
          {row.lotName && <span className="text-muted">{row.lotName}</span>}
          {row.phase === "needs_choice" && elsewhere.map((l, i) => (
            <span key={i} className="text-muted">in {l.name ?? "no location"} ({l.quantity})</span>
          ))}
          {row.phase === "error" && row.error?.includes("not in the library") && (
            <Link to="/parts/create" className="text-accent hover:underline">Create part…</Link>
          )}
        </div>
        {row.phase === "needs_mpn" && <MpnEntry id={row.id} onSubmit={onMpn} />}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <label className="sr-only" htmlFor={`qty-${row.id}`}>Bag quantity</label>
        <input
          id={`qty-${row.id}`}
          type="number"
          min={1}
          step={1}
          className="input w-24 tabular-nums"
          value={row.quantity || ""}
          disabled={busy}
          onChange={e => onQuantity(Math.max(0, Math.floor(Number(e.target.value) || 0)))}
        />
        {row.phase === "ready" && (
          <button type="button" className="btn-primary btn-sm inline-flex items-center gap-1" disabled={!(row.quantity > 0)} onClick={() => onSave()}>
            <Plus size={14} /> Add
          </button>
        )}
        {row.phase === "needs_choice" && (
          <>
            <button type="button" className="btn-primary btn-sm inline-flex items-center gap-1" onClick={() => onSave("move")}>
              <ArrowRightLeft size={14} /> Move here
            </button>
            <button type="button" className="btn btn-sm" onClick={() => onSave("add_new")}>Add as new bag</button>
          </>
        )}
        {dirty && (
          <button type="button" className="btn-primary btn-sm" disabled={!(row.quantity >= 0)} onClick={onUpdateCount}>
            Update count
          </button>
        )}
        {row.phase === "saved" && row.result?.status === "added" && !dirty && (
          <button type="button" className="btn btn-sm inline-flex items-center gap-1" title="Set this bag's count to 0" onClick={onUndo}>
            <Undo2 size={14} /> Undo
          </button>
        )}
        {!busy && (
          <button type="button" className="btn btn-sm" aria-label="Remove from list" title="Remove from list" onClick={onDismiss}>
            <Trash2 size={14} />
          </button>
        )}
      </div>
    </div>
  );
}
