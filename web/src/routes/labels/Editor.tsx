/**
 * The label editor: stock form + palette + canvas + property panel, with
 * Save, Test print and a JScript preview.
 *
 * Ported from the sibling skladVA project
 * (/mnt/data/WORK/sklad, `frontend/src/routes/labels/Editor.tsx`) — same
 * "immutable working copy, Save POSTs or PATCHes" shape. Added here: the
 * zoom control, the dirty guard, and the JScript panel, which renders the
 * server's own `GET /{id}/jscript` output so the operator can see exactly
 * what will be sent to the printer rather than trusting the canvas.
 */
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Code2, Printer, Save, ZoomIn, ZoomOut } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import Canvas, { previewValue } from "./Canvas";
import Palette from "./Palette";
import PropertyPanel from "./PropertyPanel";
import {
  printErrorMessage,
  useCreateTemplate,
  usePrintLabel,
  useTemplateJscript,
  useUpdateTemplate,
} from "./data";
import { makeElement } from "./factory";
import {
  DEFAULT_GRID_MM,
  PX_PER_MM,
  ZOOM_STEPS,
  sampleContext,
} from "./geometry";
import {
  ENTITY_TYPE_LABELS,
  LIMITS,
  PRINT_METHODS,
  type ElementKind,
  type LabelElement,
  type LabelTemplate,
  type PrintMethod,
  type TemplateDraft,
} from "./types";

interface EditorProps {
  initial: TemplateDraft;
  onClose: () => void;
  onSaved: (template: LabelTemplate) => void;
}

export default function Editor({ initial, onClose, onSaved }: EditorProps) {
  const [draft, setDraft] = useState<TemplateDraft>(initial);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [snapEnabled, setSnapEnabled] = useState(true);
  const [showGrid, setShowGrid] = useState(true);
  const [zoom, setZoom] = useState<number>(PX_PER_MM);
  const [showJscript, setShowJscript] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDraft(initial);
    setSelectedId(null);
    setDirty(false);
    setError(null);
  }, [initial]);

  const create = useCreateTemplate();
  const update = useUpdateTemplate();
  const print = usePrintLabel();
  const jscript = useTemplateJscript(draft.id, showJscript && !dirty);

  const isNew = draft.id === null;
  const sample = useMemo(
    () => sampleContext(draft.entity_type, { origin: window.location.origin }),
    [draft.entity_type],
  );
  const selected = useMemo(
    () => draft.elements.find((el) => el.id === selectedId) ?? null,
    [draft.elements, selectedId],
  );

  // ----- immutable mutators -----

  function patchTemplate(patch: Partial<TemplateDraft>) {
    setDraft((prev) => ({ ...prev, ...patch }));
    setDirty(true);
  }

  function addElement(kind: ElementKind) {
    if (draft.elements.length >= LIMITS.MAX_ELEMENTS) {
      toast.error(`A template can hold at most ${LIMITS.MAX_ELEMENTS} elements.`);
      return;
    }
    const el = makeElement(kind, 2, 2);
    setDraft((prev) => ({ ...prev, elements: [...prev.elements, el] }));
    setSelectedId(el.id);
    setDirty(true);
  }

  function moveElement(id: string, x_mm: number, y_mm: number) {
    setDraft((prev) => ({
      ...prev,
      elements: prev.elements.map((el) =>
        el.id === id ? { ...el, x_mm, y_mm } : el,
      ),
    }));
    setDirty(true);
  }

  function patchElement(id: string, patch: Partial<LabelElement>) {
    setDraft((prev) => ({
      ...prev,
      elements: prev.elements.map((el) =>
        el.id === id ? ({ ...el, ...patch } as LabelElement) : el,
      ),
    }));
    setDirty(true);
  }

  function deleteElement(id: string) {
    setDraft((prev) => ({
      ...prev,
      elements: prev.elements.filter((el) => el.id !== id),
    }));
    setSelectedId(null);
    setDirty(true);
  }

  // ----- actions -----

  async function save() {
    setError(null);
    if (!draft.name.trim()) {
      const message = "Give the template a name before saving.";
      setError(message);
      toast.error(message);
      return;
    }
    try {
      const saved = isNew
        ? await create.mutateAsync(draft)
        : await update.mutateAsync({ id: draft.id as string, draft });
      setDraft({ ...saved });
      setDirty(false);
      onSaved(saved);
      toast.success(isNew ? "Template created." : "Template saved.");
    } catch (err) {
      const message =
        err instanceof ApiError ? err.userMessage : "Could not save the template.";
      setError(message);
      toast.error(message);
    }
  }

  async function testPrint() {
    setError(null);
    if (isNew || dirty) {
      const message = "Save the template before test printing.";
      setError(message);
      toast.error(message);
      return;
    }
    try {
      const job = await print.mutateAsync({ templateId: draft.id as string });
      toast.success(`Test label sent to the printer (job ${job.status}).`);
    } catch (err) {
      const message = printErrorMessage(err);
      setError(message);
      toast.error(message);
    }
  }

  const saving = create.isPending || update.isPending;
  const zoomIndex = ZOOM_STEPS.indexOf(zoom as (typeof ZOOM_STEPS)[number]);

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn-ghost btn-sm" onClick={onClose}>
          <ArrowLeft size={15} />
          Back
        </button>
        <div className="min-w-0">
          <h2 className="truncate text-base font-semibold">
            {isNew ? "New template" : draft.name || "Untitled template"}
          </h2>
          <p className="text-xs text-muted">
            {ENTITY_TYPE_LABELS[draft.entity_type]} label
            {dirty && <span className="ml-2 text-warning">Unsaved changes</span>}
          </p>
        </div>
        <div className="ml-auto flex flex-wrap gap-2">
          <button
            type="button"
            className={cn("btn", showJscript && "btn-primary")}
            aria-pressed={showJscript}
            onClick={() => setShowJscript((open) => !open)}
          >
            <Code2 size={15} />
            JScript
          </button>
          <button
            type="button"
            className="btn"
            onClick={testPrint}
            disabled={print.isPending}
          >
            <Printer size={15} />
            {print.isPending ? "Printing…" : "Test print"}
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={save}
            disabled={saving}
          >
            <Save size={15} />
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </header>

      {error && (
        <div
          role="alert"
          className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger"
        >
          {error}
        </div>
      )}

      <StockForm draft={draft} onPatch={patchTemplate} />

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[190px_minmax(0,1fr)_270px]">
        <div className="space-y-3">
          <Palette onAdd={addElement} />
          <div className="card space-y-2 p-3 text-sm">
            <h3 className="section-title">Canvas</h3>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={snapEnabled}
                onChange={() => setSnapEnabled((on) => !on)}
              />
              Snap to {DEFAULT_GRID_MM} mm grid
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={showGrid}
                onChange={() => setShowGrid((on) => !on)}
              />
              Show grid
            </label>
            <div className="flex items-center gap-2 pt-1">
              <button
                type="button"
                className="btn-ghost btn-sm"
                aria-label="Zoom out"
                disabled={zoomIndex <= 0}
                onClick={() => setZoom(ZOOM_STEPS[Math.max(0, zoomIndex - 1)])}
              >
                <ZoomOut size={14} />
              </button>
              <span className="text-xs text-muted">{zoom} px/mm</span>
              <button
                type="button"
                className="btn-ghost btn-sm"
                aria-label="Zoom in"
                disabled={zoomIndex >= ZOOM_STEPS.length - 1}
                onClick={() =>
                  setZoom(ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, zoomIndex + 1)])
                }
              >
                <ZoomIn size={14} />
              </button>
            </div>
          </div>
        </div>

        <div className="min-w-0 space-y-3">
          <Canvas
            template={draft}
            sample={sample}
            selectedId={selectedId}
            gridMm={DEFAULT_GRID_MM}
            showGrid={showGrid}
            snapEnabled={snapEnabled}
            pxPerMm={zoom}
            onSelect={setSelectedId}
            onMove={moveElement}
            onResize={patchElement}
          />
          <p className="text-xs text-muted">
            Previewed against sample data. QR and barcode blocks show the real
            printed footprint — the printer generates the symbols themselves
            from the JScript.
          </p>

          {showJscript && (
            <div className="card p-3">
              <h3 className="section-title mb-2">Rendered JScript (sample data)</h3>
              {isNew || dirty ? (
                <p className="text-xs text-muted">
                  Save the template to render its JScript.
                </p>
              ) : jscript.isError ? (
                <p className="text-xs text-danger" role="alert">
                  {jscript.error instanceof ApiError
                    ? jscript.error.userMessage
                    : "Could not render this template."}
                </p>
              ) : (
                <pre className="max-h-64 overflow-auto rounded-md bg-panel2 p-2 font-mono text-[11px] leading-relaxed text-text">
                  {jscript.isLoading ? "Rendering…" : jscript.data?.jscript}
                </pre>
              )}
            </div>
          )}
        </div>

        <PropertyPanel
          element={selected}
          entity={draft.entity_type}
          resolved={selected ? previewValue(selected, sample) : ""}
          onChange={(patch) => selected && patchElement(selected.id, patch)}
          onDelete={() => selected && deleteElement(selected.id)}
        />
      </div>
    </section>
  );
}

/**
 * Media + print-engine settings. These map 1:1 onto the `label_templates`
 * geometry columns that build the JScript job header (`label_render.render`),
 * so the bounds mirror `printing/schemas.py`.
 */
function StockForm({
  draft,
  onPatch,
}: {
  draft: TemplateDraft;
  onPatch: (patch: Partial<TemplateDraft>) => void;
}) {
  return (
    <div className="card grid grid-cols-1 gap-3 p-4 sm:grid-cols-4 lg:grid-cols-8">
      <label className="col-span-2 block">
        <span className="label">Name</span>
        <input
          className="input"
          value={draft.name}
          maxLength={200}
          onChange={(event) => onPatch({ name: event.target.value })}
        />
      </label>
      <NumField
        label="Width (mm)"
        value={draft.width_mm}
        min={1}
        max={LIMITS.MM_MAX}
        onChange={(v) => onPatch({ width_mm: v })}
      />
      <NumField
        label="Height (mm)"
        value={draft.height_mm}
        min={1}
        max={LIMITS.MM_MAX}
        onChange={(v) => onPatch({ height_mm: v })}
      />
      <NumField
        label="Gap (mm)"
        value={draft.gap_mm}
        min={0}
        max={LIMITS.GAP_MAX}
        onChange={(v) => onPatch({ gap_mm: v })}
      />
      <NumField
        label="Heat"
        value={draft.heat}
        step={1}
        min={0}
        max={LIMITS.HEAT_MAX}
        onChange={(v) => onPatch({ heat: v })}
      />
      <NumField
        label="DPI"
        value={draft.dpi}
        step={1}
        min={LIMITS.DPI_MIN}
        max={LIMITS.DPI_MAX}
        onChange={(v) => onPatch({ dpi: v })}
      />
      <label className="block">
        <span className="label">Method</span>
        <select
          className="input"
          value={draft.method}
          onChange={(event) =>
            onPatch({ method: event.target.value as PrintMethod })
          }
        >
          {PRINT_METHODS.map((method) => (
            <option key={method} value={method}>
              {method === "T" ? "T — ribbon" : "D — direct"}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

function NumField({
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
        value={value}
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
