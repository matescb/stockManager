import { useCallback, useEffect, useId, useRef, useState } from "react";
import { PreviewUnavailable } from "./PreviewBoundary";

/**
 * A collapsible fixed-height card showing one server-rendered preview SVG,
 * with wheel-zoom and drag-to-pan.
 *
 * `SymbolPreview` and `FootprintPreview` are this component with a URL.
 * The SVG comes from the backend's kicad-cli render routes
 * (`domain/eda/render.py`), so it is drawn exactly as KiCad draws it.
 *
 * It is loaded through an `<img>`, not inlined. The SVG is generated from
 * attacker-supplied stored geometry, and an SVG placed in the document can
 * run script — an `<img>` never does, which is the whole reason to use one
 * here. The same-origin request carries the session cookie, so the
 * workspace-scoped route authorises normally.
 *
 * Pan/zoom is a plain CSS transform on a wrapper (no extra dependency, and
 * it works with the `<img>` the security stance requires). Scale is bounded
 * at [1, 8]; the wheel zooms toward the cursor, the buttons toward the
 * centre, and "Fit" (or a double-click) returns to the whole-drawing view.
 */

type Props = {
  /** URL of a backend `.svg` preview route. */
  src: string;
  /** Card heading. */
  title: string;
  /** Height class for the viewer area. */
  heightClass?: string;
};

type LoadState = "loading" | "ready" | "failed";
type View = { scale: number; tx: number; ty: number };

const MIN_SCALE = 1;
const MAX_SCALE = 8;
const IDENTITY: View = { scale: 1, tx: 0, ty: 0 };

const clamp = (value: number, lo: number, hi: number) =>
  Math.min(hi, Math.max(lo, value));

export function SvgPreview({ src, title, heightClass = "h-64" }: Props) {
  const [expanded, setExpanded] = useState(true);
  const [state, setState] = useState<LoadState>("loading");
  const [view, setView] = useState<View>(IDENTITY);
  const regionId = useId();
  const stageRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);

  // A new src is a new render — reset zoom/pan and the load state so the
  // spinner shows again and a previous failure doesn't stick to the image.
  useEffect(() => {
    setState("loading");
    setView(IDENTITY);
  }, [src]);

  // Zoom toward (cx, cy) in stage coordinates, keeping that point fixed.
  const zoomAt = useCallback((cx: number, cy: number, factor: number) => {
    setView((v) => {
      const scale = clamp(v.scale * factor, MIN_SCALE, MAX_SCALE);
      if (scale === v.scale) return v;
      const contentX = (cx - v.tx) / v.scale;
      const contentY = (cy - v.ty) / v.scale;
      return { scale, tx: cx - contentX * scale, ty: cy - contentY * scale };
    });
  }, []);

  // Wheel needs a non-passive native listener to preventDefault the page
  // scroll; React's synthetic onWheel is passive in some browsers.
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      zoomAt(e.clientX - rect.left, e.clientY - rect.top, e.deltaY < 0 ? 1.1 : 1 / 1.1);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt, expanded, state]);

  const zoomFromButton = (factor: number) => {
    const el = stageRef.current;
    zoomAt((el?.clientWidth ?? 0) / 2, (el?.clientHeight ?? 0) / 2, factor);
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    drag.current = { x: e.clientX, y: e.clientY };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    drag.current = { x: e.clientX, y: e.clientY };
    setView((v) => ({ ...v, tx: v.tx + dx, ty: v.ty + dy }));
  };
  const endDrag = (e: React.PointerEvent) => {
    drag.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* pointer was never captured */
    }
  };

  const zoomed = view.scale !== 1 || view.tx !== 0 || view.ty !== 0;

  return (
    <section className="card p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">{title}</h4>
        <button
          type="button"
          className="btn btn-sm"
          aria-expanded={expanded}
          aria-controls={regionId}
          onClick={() => setExpanded((open) => !open)}
        >
          {expanded ? "Hide" : "Show"}
        </button>
      </div>
      {expanded && (
        <div
          id={regionId}
          className={`${heightClass} relative overflow-hidden rounded border bg-white`}
        >
          {state !== "failed" && (
            <div
              ref={stageRef}
              className="absolute inset-0 cursor-grab touch-none active:cursor-grabbing"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={endDrag}
              onPointerLeave={endDrag}
              onDoubleClick={() => setView(IDENTITY)}
            >
              <div
                data-testid="svg-preview-stage"
                style={{
                  transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.scale})`,
                  transformOrigin: "0 0",
                  width: "100%",
                  height: "100%",
                }}
              >
                <img
                  key={src}
                  src={src}
                  alt={`${title} rendered by KiCad`}
                  className="pointer-events-none h-full w-full select-none object-contain"
                  draggable={false}
                  onLoad={() => setState("ready")}
                  onError={() => setState("failed")}
                  data-testid="svg-preview-img"
                />
              </div>
            </div>
          )}
          {state === "ready" && (
            <div className="absolute right-2 top-2 flex gap-1">
              <button
                type="button"
                className="btn btn-sm"
                aria-label="Zoom in"
                onClick={() => zoomFromButton(1.25)}
              >
                +
              </button>
              <button
                type="button"
                className="btn btn-sm"
                aria-label="Zoom out"
                onClick={() => zoomFromButton(1 / 1.25)}
              >
                −
              </button>
              <button
                type="button"
                className="btn btn-sm"
                aria-label="Reset view"
                disabled={!zoomed}
                onClick={() => setView(IDENTITY)}
              >
                Fit
              </button>
            </div>
          )}
          {state === "loading" && (
            <div className="absolute inset-0 flex items-center justify-center text-sm text-muted">
              Loading preview…
            </div>
          )}
          {state === "failed" && <PreviewUnavailable />}
        </div>
      )}
    </section>
  );
}
