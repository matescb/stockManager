/**
 * Element palette. Clicking a tile appends a fresh element of that kind; the
 * editor owns placement and selection.
 *
 * Ported from the sibling skladVA project
 * (/mnt/data/WORK/sklad, `frontend/src/routes/labels/Palette.tsx`), minus its
 * `image` tile — this codebase's renderer has no image element, so offering
 * one would let an operator build a template that prints nothing.
 */
import { Barcode, Minus, QrCode, Type } from "lucide-react";
import { ELEMENT_KIND_LABELS, type ElementKind } from "./types";

const TILES: ReadonlyArray<{ kind: ElementKind; icon: typeof QrCode }> = [
  { kind: "qr", icon: QrCode },
  { kind: "text", icon: Type },
  { kind: "barcode1d", icon: Barcode },
  { kind: "handwriting", icon: Minus },
];

export default function Palette({
  onAdd,
  disabled,
}: {
  onAdd: (kind: ElementKind) => void;
  disabled?: boolean;
}) {
  return (
    <div className="card p-3">
      <h3 className="section-title mb-2">Add element</h3>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {TILES.map(({ kind, icon: Icon }) => (
          <button
            key={kind}
            type="button"
            className="btn-ghost flex-col gap-1 py-3"
            disabled={disabled}
            onClick={() => onAdd(kind)}
          >
            <Icon size={18} />
            <span className="text-xs">{ELEMENT_KIND_LABELS[kind]}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
