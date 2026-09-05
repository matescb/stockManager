/**
 * The mm-accurate WYSIWYG canvas.
 *
 * Ported from the sibling skladVA project
 * (/mnt/data/WORK/sklad, `frontend/src/routes/labels/Canvas.tsx`): the ruler,
 * the pointer-drag move/resize model and the per-kind element preview are its
 * structure. Adapted here for this codebase's element kinds (no `image`), its
 * Tailwind token set, and keyboard nudging so the designer is usable without
 * a pointer.
 *
 * Everything is laid out in millimetres and multiplied by a single `pxPerMm`
 * zoom at paint time — see `geometry.ts` for why the model is mm rather than
 * pixels.
 */
import { useCallback, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { cn } from "@/lib/cn";
import QrPreview from "./QrPreview";
import {
  PT_TO_MM,
  PX_PER_MM,
  clampToLabel,
  elementFootprint,
  mmToPx,
  previewSanitize,
  pxToMm,
  resolveQrPayload,
  resolveTextValue,
  snapMm,
  snapPoint,
  type SampleContext,
} from "./geometry";
import type { LabelElement, TemplateDraft } from "./types";

interface CanvasProps {
  template: TemplateDraft;
  sample: SampleContext;
  selectedId: string | null;
  gridMm: number;
  showGrid: boolean;
  snapEnabled: boolean;
  pxPerMm?: number;
  onSelect: (id: string | null) => void;
  onMove: (id: string, x_mm: number, y_mm: number) => void;
  onResize: (id: string, patch: Partial<LabelElement>) => void;
}

type DragState =
  | {
      mode: "move";
      id: string;
      startX: number;
      startY: number;
      origX: number;
      origY: number;
    }
  | {
      mode: "resize";
      id: string;
      startX: number;
      startY: number;
      origW: number;
      origH: number;
    }
  | null;

/** The display string for an element, resolved and sanitised as the printer will see it. */
export function previewValue(el: LabelElement, sample: SampleContext): string {
  if (el.kind === "handwriting") return "";
  if (el.kind === "qr") return previewSanitize(resolveQrPayload(el, sample));
  return previewSanitize(resolveTextValue(el, sample));
}

export default function Canvas({
  template,
  sample,
  selectedId,
  gridMm,
  showGrid,
  snapEnabled,
  pxPerMm = PX_PER_MM,
  onSelect,
  onMove,
  onResize,
}: CanvasProps) {
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const [drag, setDrag] = useState<DragState>(null);

  const widthPx = mmToPx(template.width_mm, pxPerMm);
  const heightPx = mmToPx(template.height_mm, pxPerMm);
  const effectiveGrid = snapEnabled ? gridMm : 0;

  const onPointerMove = useCallback(
    (event: ReactPointerEvent) => {
      if (!drag) return;
      const dxMm = pxToMm(event.clientX - drag.startX, pxPerMm);
      const dyMm = pxToMm(event.clientY - drag.startY, pxPerMm);
      const el = template.elements.find((candidate) => candidate.id === drag.id);
      if (!el) return;

      if (drag.mode === "move") {
        const footprint = elementFootprint(el, previewValue(el, sample));
        const next = snapPoint(
          { x: drag.origX + dxMm, y: drag.origY + dyMm },
          effectiveGrid,
        );
        const clamped = clampToLabel(next, footprint, template);
        onMove(drag.id, clamped.x, clamped.y);
        return;
      }

      const w = Math.max(0.5, snapMm(drag.origW + dxMm, effectiveGrid));
      const h = Math.max(0.1, snapMm(drag.origH + dyMm, effectiveGrid));
      if (el.kind === "handwriting") {
        onResize(drag.id, { w_mm: w, h_mm: h } as Partial<LabelElement>);
      } else if (el.kind === "barcode1d") {
        onResize(drag.id, { height_mm: h } as Partial<LabelElement>);
      } else if (el.kind === "qr") {
        // A QR's only size knob is the module size; map the drag to it via
        // the symbol's real module count so the grip tracks the pointer.
        const modules = Math.max(
          1,
          elementFootprint(el, previewValue(el, sample)).w / el.dotsize_mm,
        );
        onResize(drag.id, {
          dotsize_mm: Math.max(0.1, Math.round((w / modules) * 100) / 100),
        } as Partial<LabelElement>);
      }
    },
    [drag, effectiveGrid, onMove, onResize, pxPerMm, sample, template],
  );

  const endDrag = useCallback(() => setDrag(null), []);

  function nudge(el: LabelElement, dxMm: number, dyMm: number) {
    const footprint = elementFootprint(el, previewValue(el, sample));
    const next = snapPoint(
      { x: el.x_mm + dxMm, y: el.y_mm + dyMm },
      effectiveGrid,
    );
    const clamped = clampToLabel(next, footprint, template);
    onMove(el.id, clamped.x, clamped.y);
  }

  return (
    <div className="overflow-auto rounded-lg border border-border bg-panel2 p-3">
      <Ruler lengthMm={template.width_mm} pxPerMm={pxPerMm} orientation="h" />
      <div className="flex">
        <Ruler lengthMm={template.height_mm} pxPerMm={pxPerMm} orientation="v" />
        <div
          ref={surfaceRef}
          data-testid="label-canvas"
          className="relative touch-none select-none bg-white shadow-inner ring-1 ring-black/20"
          style={{
            width: widthPx,
            height: heightPx,
            backgroundImage: showGrid ? GRID_IMAGE : undefined,
            backgroundSize: showGrid
              ? `${mmToPx(gridMm, pxPerMm)}px ${mmToPx(gridMm, pxPerMm)}px`
              : undefined,
          }}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerLeave={endDrag}
          onMouseDown={(event) => {
            if (event.target === surfaceRef.current) onSelect(null);
          }}
          role="application"
          aria-label={`Label canvas, ${template.width_mm} by ${template.height_mm} millimetres`}
        >
          {template.elements.map((el) => (
            <ElementView
              key={el.id}
              el={el}
              value={previewValue(el, sample)}
              pxPerMm={pxPerMm}
              selected={el.id === selectedId}
              onSelect={() => onSelect(el.id)}
              onNudge={(dxMm, dyMm) => nudge(el, dxMm, dyMm)}
              gridMm={effectiveGrid || 0.5}
              onStartMove={(event) => {
                event.stopPropagation();
                (event.target as Element).setPointerCapture?.(event.pointerId);
                onSelect(el.id);
                setDrag({
                  mode: "move",
                  id: el.id,
                  startX: event.clientX,
                  startY: event.clientY,
                  origX: el.x_mm,
                  origY: el.y_mm,
                });
              }}
              onStartResize={(event) => {
                event.stopPropagation();
                (event.target as Element).setPointerCapture?.(event.pointerId);
                const footprint = elementFootprint(el, previewValue(el, sample));
                setDrag({
                  mode: "resize",
                  id: el.id,
                  startX: event.clientX,
                  startY: event.clientY,
                  origW: footprint.w,
                  origH: footprint.h,
                });
              }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

/** A 1px hairline grid drawn with two gradients — cheap and zoom-independent. */
const GRID_IMAGE =
  "linear-gradient(to right, rgba(0,0,0,0.10) 1px, transparent 1px)," +
  "linear-gradient(to bottom, rgba(0,0,0,0.10) 1px, transparent 1px)";

// ---------------------------------------------------------------------
// Ruler
// ---------------------------------------------------------------------

function Ruler({
  lengthMm,
  pxPerMm,
  orientation,
}: {
  lengthMm: number;
  pxPerMm: number;
  orientation: "h" | "v";
}) {
  const step = lengthMm > 120 ? 20 : lengthMm > 60 ? 10 : 5;
  const ticks: number[] = [];
  for (let mm = 0; mm <= Math.ceil(lengthMm); mm += step) ticks.push(mm);
  const lengthPx = mmToPx(lengthMm, pxPerMm);

  if (orientation === "h") {
    return (
      <div className="relative mb-1 ml-7 h-4" style={{ width: lengthPx }} aria-hidden="true">
        {ticks.map((mm) => (
          <div
            key={mm}
            className="absolute top-0 h-3 border-l border-border pl-0.5 text-[9px] leading-none text-muted"
            style={{ left: mmToPx(mm, pxPerMm) }}
          >
            {mm}
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="relative mr-1 w-6" style={{ height: lengthPx }} aria-hidden="true">
      {ticks.map((mm) => (
        <div
          key={mm}
          className="absolute right-0 w-4 border-t border-border pr-0.5 text-right text-[9px] leading-none text-muted"
          style={{ top: mmToPx(mm, pxPerMm) }}
        >
          {mm}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------
// Element view
// ---------------------------------------------------------------------

interface ElementViewProps {
  el: LabelElement;
  value: string;
  pxPerMm: number;
  selected: boolean;
  gridMm: number;
  onSelect: () => void;
  onNudge: (dxMm: number, dyMm: number) => void;
  onStartMove: (event: ReactPointerEvent) => void;
  onStartResize: (event: ReactPointerEvent) => void;
}

const NUDGE_KEYS: Record<string, [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
};

function ElementView({
  el,
  value,
  pxPerMm,
  selected,
  gridMm,
  onSelect,
  onNudge,
  onStartMove,
  onStartResize,
}: ElementViewProps) {
  // Text is the one kind with no draggable size: its box comes from the font.
  const resizable = el.kind !== "text";

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`${el.kind} element at ${el.x_mm} by ${el.y_mm} millimetres`}
      aria-pressed={selected}
      className={cn(
        "absolute cursor-move focus:outline-none",
        selected
          ? "outline outline-2 outline-accent z-10"
          : "hover:outline hover:outline-1 hover:outline-accent/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent/70",
      )}
      style={{
        left: mmToPx(el.x_mm, pxPerMm),
        top: mmToPx(el.y_mm, pxPerMm),
        transform: el.rotation ? `rotate(${el.rotation}deg)` : undefined,
        transformOrigin: "top left",
      }}
      onPointerDown={onStartMove}
      onKeyDown={(event) => {
        const delta = NUDGE_KEYS[event.key];
        if (delta) {
          event.preventDefault();
          const stepMm = (event.shiftKey ? 5 : 1) * gridMm;
          onNudge(delta[0] * stepMm, delta[1] * stepMm);
          return;
        }
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
      onFocus={onSelect}
    >
      <ElementBody el={el} value={value} pxPerMm={pxPerMm} />
      {selected && resizable && (
        <span
          role="button"
          tabIndex={-1}
          aria-label={`Resize ${el.kind} element`}
          className="absolute -bottom-1.5 -right-1.5 h-3 w-3 cursor-se-resize rounded-sm bg-accent"
          onPointerDown={onStartResize}
        />
      )}
    </div>
  );
}

/** cab device font id -> a CSS stack that reads similarly on screen. */
function fontStyle(font: number | string): { fontFamily: string; fontWeight: number } {
  if (font === 596 || font === "596") {
    return { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontWeight: 400 };
  }
  if (font === 5 || font === "5") {
    return { fontFamily: "Helvetica, Arial, sans-serif", fontWeight: 700 };
  }
  if (typeof font === "string" && font.trim() !== "" && !/^\d+$/.test(font)) {
    // A downloaded TrueType font on the printer. We have no way to know what
    // it looks like, so render the neutral face and let the name speak.
    return { fontFamily: "Helvetica, Arial, sans-serif", fontWeight: 400 };
  }
  return { fontFamily: "Helvetica, Arial, sans-serif", fontWeight: 400 };
}

function ElementBody({
  el,
  value,
  pxPerMm,
}: {
  el: LabelElement;
  value: string;
  pxPerMm: number;
}) {
  switch (el.kind) {
    case "qr":
      return (
        <QrPreview
          value={value}
          dotsizeMm={el.dotsize_mm}
          ec={el.ec}
          pxPerMm={pxPerMm}
        />
      );
    case "text": {
      const { fontFamily, fontWeight } = fontStyle(el.font);
      return (
        <span
          className="block whitespace-nowrap leading-none text-black"
          style={{
            // 1pt of type is size_pt * 0.352778 mm tall; the cab head prints
            // the same physical size, so the preview uses the same maths.
            fontSize: mmToPx(el.size_pt * PT_TO_MM, pxPerMm),
            fontFamily,
            fontWeight,
          }}
        >
          {value || " "}
        </span>
      );
    }
    case "barcode1d": {
      const widthPx = mmToPx(
        elementFootprint(el, value).w,
        pxPerMm,
      );
      const heightPx = mmToPx(Math.max(1, el.height_mm), pxPerMm);
      return <BarcodeStripes width={widthPx} height={heightPx} payload={value} />;
    }
    case "handwriting": {
      // `G ... L:length,width` — w_mm is the LENGTH, h_mm the THICKNESS.
      return (
        <span
          className="block bg-black"
          style={{
            width: mmToPx(Math.max(0.5, el.w_mm), pxPerMm),
            height: Math.max(1, mmToPx(Math.max(0.05, el.h_mm), pxPerMm)),
          }}
        />
      );
    }
  }
}

/**
 * A deterministic stripe pattern that reads as "a barcode". The printer
 * generates the real symbology from the JScript `B` command, so this is a
 * placeholder for placement only — same reasoning as `QrPreview`.
 */
function BarcodeStripes({
  width,
  height,
  payload,
}: {
  width: number;
  height: number;
  payload: string;
}) {
  const bars: Array<{ x: number; w: number }> = [];
  let seed = 7;
  for (let i = 0; i < payload.length; i += 1) {
    seed = (seed * 31 + payload.charCodeAt(i)) & 0x7fffffff;
  }
  let x = 0;
  let dark = true;
  while (x < width) {
    seed = (Math.imul(seed, 1103515245) + 12345) & 0x7fffffff;
    const w = 1 + (seed % 3);
    if (dark) bars.push({ x, w });
    dark = !dark;
    x += w;
  }
  return (
    <svg
      width={width}
      height={height}
      className="block bg-white"
      shapeRendering="crispEdges"
      role="img"
      aria-label="Barcode placeholder"
    >
      {bars.map((bar) => (
        <rect key={bar.x} x={bar.x} y={0} width={bar.w} height={height} fill="#000000" />
      ))}
    </svg>
  );
}
