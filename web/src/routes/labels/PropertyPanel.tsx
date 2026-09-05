/**
 * Right-hand property panel for the selected element.
 *
 * Ported from the sibling skladVA project
 * (/mnt/data/WORK/sklad, `frontend/src/routes/labels/PropertyPanel.tsx`) —
 * same "every edit is an immutable patch" contract, this codebase's `input` /
 * `label` / `btn` utilities instead of its own, and the binding dropdown
 * driven by the entity type's real server-side context rather than a fixed
 * list.
 */
import { Trash2 } from "lucide-react";
import { cn } from "@/lib/cn";
import {
  BARCODE_TYPES,
  DEVICE_FONTS,
  ELEMENT_KIND_LABELS,
  LIMITS,
  QR_EC_LEVELS,
  bindingsFor,
  type LabelElement,
  type LabelEntityType,
  type QrEcLevel,
} from "./types";

interface PropertyPanelProps {
  element: LabelElement | null;
  entity: LabelEntityType;
  /** The value this element currently resolves to, shown as a preview hint. */
  resolved: string;
  onChange: (patch: Partial<LabelElement>) => void;
  onDelete: () => void;
}

export default function PropertyPanel({
  element,
  entity,
  resolved,
  onChange,
  onDelete,
}: PropertyPanelProps) {
  if (!element) {
    return (
      <aside className="card p-4 text-sm text-muted">
        Select an element on the label to edit it, or add one from the palette.
      </aside>
    );
  }

  return (
    <aside className="card space-y-3 p-4">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold">{ELEMENT_KIND_LABELS[element.kind]}</h3>
        <button
          type="button"
          className="btn-danger btn-sm ml-auto"
          onClick={onDelete}
        >
          <Trash2 size={13} />
          Remove
        </button>
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <NumberField
          label="X (mm)"
          value={element.x_mm}
          min={-LIMITS.MM_MAX}
          max={LIMITS.MM_MAX}
          onChange={(v) => onChange({ x_mm: v })}
        />
        <NumberField
          label="Y (mm)"
          value={element.y_mm}
          min={-LIMITS.MM_MAX}
          max={LIMITS.MM_MAX}
          onChange={(v) => onChange({ y_mm: v })}
        />
        <SelectField
          label="Rotation"
          value={String(element.rotation)}
          options={["0", "90", "180", "270"]}
          onChange={(v) => onChange({ rotation: Number(v) })}
        />
      </div>

      <KindFields element={element} entity={entity} onChange={onChange} />

      {element.kind !== "handwriting" && (
        <p className="rounded-md bg-panel2 px-2 py-1.5 text-xs text-muted">
          <span className="text-muted">Preview value: </span>
          <span className="font-mono text-text break-all">{resolved || "—"}</span>
        </p>
      )}
    </aside>
  );
}

function KindFields({
  element,
  entity,
  onChange,
}: {
  element: LabelElement;
  entity: LabelEntityType;
  onChange: (patch: Partial<LabelElement>) => void;
}) {
  switch (element.kind) {
    case "qr":
      return (
        <div className="space-y-2">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <NumberField
              label="Module (mm)"
              value={element.dotsize_mm}
              step={0.05}
              min={0.1}
              onChange={(v) => onChange({ dotsize_mm: v })}
            />
            <SelectField
              label="Error correction"
              value={element.ec}
              options={[...QR_EC_LEVELS]}
              onChange={(v) => onChange({ ec: v as QrEcLevel })}
            />
          </div>
          <SourceFields
            element={element}
            entity={entity}
            onChange={onChange}
            defaultBinding="url"
          />
        </div>
      );
    case "text":
      return (
        <div className="space-y-2">
          <SourceFields
            element={element}
            entity={entity}
            onChange={onChange}
            defaultBinding="name"
          />
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <SelectField
              label="Font"
              value={String(element.font)}
              options={DEVICE_FONTS.map((f) => String(f.value))}
              optionLabel={(v) =>
                DEVICE_FONTS.find((f) => String(f.value) === v)?.label ?? v
              }
              onChange={(v) => onChange({ font: Number(v) })}
            />
            <NumberField
              label="Size (pt)"
              value={element.size_pt}
              step={1}
              min={1}
              onChange={(v) => onChange({ size_pt: v })}
            />
          </div>
        </div>
      );
    case "barcode1d":
      return (
        <div className="space-y-2">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <SelectField
              label="Symbology"
              value={element.bc_type}
              options={[...BARCODE_TYPES]}
              onChange={(v) => onChange({ bc_type: v })}
            />
            <NumberField
              label="Height (mm)"
              value={element.height_mm}
              step={0.5}
              min={1}
              onChange={(v) => onChange({ height_mm: v })}
            />
            <NumberField
              label="Narrow bar (mm)"
              value={element.ne_mm}
              step={0.05}
              min={0.05}
              onChange={(v) => onChange({ ne_mm: v })}
            />
          </div>
          <SourceFields
            element={element}
            entity={entity}
            onChange={onChange}
            defaultBinding="code"
          />
        </div>
      );
    case "handwriting":
      return (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <NumberField
            label="Length (mm)"
            value={element.w_mm}
            step={0.5}
            min={0.5}
            onChange={(v) => onChange({ w_mm: v })}
          />
          <NumberField
            label="Thickness (mm)"
            value={element.h_mm}
            step={0.05}
            min={0.05}
            onChange={(v) => onChange({ h_mm: v })}
          />
        </div>
      );
  }
}

/**
 * The literal-vs-binding switch.
 *
 * The two are mutually exclusive on purpose, and the order matters:
 * `label_render._resolve_text` only falls through to `binding` when `text` is
 * ABSENT (not merely empty). Switching to "Binding" therefore clears `text`
 * to `undefined` rather than to `""`, or the element would render blank.
 */
function SourceFields({
  element,
  entity,
  onChange,
  defaultBinding,
}: {
  element: LabelElement & { text?: string | null; binding?: string | null };
  entity: LabelEntityType;
  onChange: (patch: Partial<LabelElement>) => void;
  defaultBinding: string;
}) {
  const isLiteral = element.text != null && element.text !== "";
  const bindings = bindingsFor(entity);
  const current = element.binding || defaultBinding;

  return (
    <div className="space-y-2">
      <div className="flex gap-2" role="group" aria-label="Value source">
        <button
          type="button"
          className={cn("btn-sm", isLiteral ? "btn-primary" : "btn-ghost")}
          aria-pressed={isLiteral}
          onClick={() => onChange({ binding: undefined, text: element.text || "Text" } as Partial<LabelElement>)}
        >
          Literal
        </button>
        <button
          type="button"
          className={cn("btn-sm", isLiteral ? "btn-ghost" : "btn-primary")}
          aria-pressed={!isLiteral}
          onClick={() => onChange({ text: undefined, binding: current } as Partial<LabelElement>)}
        >
          Field
        </button>
      </div>

      {isLiteral ? (
        <TextField
          label="Text"
          value={element.text ?? ""}
          maxLength={LIMITS.MAX_TEXT}
          hint="{{code}} and other field tokens are substituted."
          onChange={(v) => onChange({ text: v } as Partial<LabelElement>)}
        />
      ) : (
        <SelectField
          label="Field"
          value={current}
          options={bindings.includes(current) ? bindings : [current, ...bindings]}
          onChange={(v) => onChange({ binding: v } as Partial<LabelElement>)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------
// Field primitives
// ---------------------------------------------------------------------

function NumberField({
  label,
  value,
  step = 0.5,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  step?: number;
  min?: number;
  max?: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <input
        type="number"
        className="input"
        value={Number.isFinite(value) ? value : 0}
        step={step}
        min={min}
        max={max}
        onChange={(event) => {
          const next = Number(event.target.value);
          onChange(Number.isFinite(next) ? next : 0);
        }}
      />
    </label>
  );
}

function TextField({
  label,
  value,
  maxLength,
  hint,
  onChange,
}: {
  label: string;
  value: string;
  maxLength?: number;
  hint?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <input
        className="input"
        value={value}
        maxLength={maxLength}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint && <span className="mt-1 block text-[11px] text-muted">{hint}</span>}
    </label>
  );
}

function SelectField({
  label,
  value,
  options,
  optionLabel,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  optionLabel?: (value: string) => string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <select
        className="input"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {optionLabel ? optionLabel(option) : option}
          </option>
        ))}
      </select>
    </label>
  );
}
