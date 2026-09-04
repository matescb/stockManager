import { useEffect, useId, useRef, useState } from "react";
import { PreviewBoundary, PreviewUnavailable } from "./PreviewBoundary";
import type { ModelFormat, ModelViewerHandle } from "./modelRenderer";

/**
 * A collapsible fixed-height card holding one three.js 3D model view.
 *
 * The sibling of `KicanvasFrame` for 3D: STEP models are fetched as GLB
 * from the `preview.glb` route, WRL straight from `/files` (the browser
 * reads VRML natively). Everything three.js touches lives in
 * `./modelRenderer`, which this component imports **dynamically** so the
 * ~600 KB of three + loaders is a lazy chunk, fetched only when a preview
 * actually mounts — most CAD-tab visits never open one.
 *
 * The viewer is created imperatively and only while expanded, for the
 * same reasons `KicanvasFrame` gives: it owns a WebGL context and does
 * real work on mount, so a clean create-per-src beats reusing one across
 * URL changes, and `dispose()` on teardown is what frees the context.
 */

type Props = {
  /** URL of the model — the `preview.glb` route (GLB) or `/files` (WRL). */
  src: string;
  /** How `modelRenderer` should parse `src`. */
  format: ModelFormat;
  /** Card heading. */
  title: string;
  /** Height class for the viewer area. */
  heightClass?: string;
};

type LoadState = "loading" | "ready" | "failed";

export function ModelPreview({ src, format, title, heightClass = "h-64" }: Props) {
  const [expanded, setExpanded] = useState(true);
  const regionId = useId();

  return (
    <section className="card p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">{title}</h4>
        <button
          type="button"
          className="btn text-xs"
          aria-expanded={expanded}
          aria-controls={regionId}
          onClick={() => setExpanded((open) => !open)}
        >
          {expanded ? "Hide" : "Show"}
        </button>
      </div>
      {expanded && (
        <div id={regionId} className={`${heightClass} overflow-hidden rounded border`}>
          {/* Keyed on src so a new model gets a fresh boundary and a fresh
              viewer rather than a reset of the old one. */}
          <PreviewBoundary key={src} resetKey={src}>
            <Viewer src={src} format={format} />
          </PreviewBoundary>
        </div>
      )}
    </section>
  );
}

function Viewer({ src, format }: { src: string; format: ModelFormat }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<LoadState>("loading");

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    let cancelled = false;
    let handle: ModelViewerHandle | null = null;
    const controller = new AbortController();
    setState("loading");

    // Dynamic import: this is the boundary that keeps three.js out of the
    // main bundle. See the module docstring + model3dChunk.test.ts.
    import("./modelRenderer")
      .then(({ mountModelViewer }) => {
        if (cancelled || !hostRef.current) return null;
        return mountModelViewer(hostRef.current, {
          src,
          format,
          signal: controller.signal,
        });
      })
      .then((mounted) => {
        if (cancelled) {
          mounted?.dispose();
          return;
        }
        if (mounted) {
          handle = mounted;
          setState("ready");
        }
      })
      .catch(() => {
        if (!cancelled) setState("failed");
      });

    return () => {
      cancelled = true;
      controller.abort();
      // Releases the WebGL context. Leaving it attached leaks one per
      // remount.
      handle?.dispose();
    };
  }, [src, format]);

  if (state === "failed") return <PreviewUnavailable message="3D preview unavailable" />;

  return (
    <div className="relative h-full w-full">
      <div ref={hostRef} className="h-full w-full" data-testid="model-host" />
      {state === "loading" && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-muted">
          Loading 3D preview…
        </div>
      )}
    </div>
  );
}
