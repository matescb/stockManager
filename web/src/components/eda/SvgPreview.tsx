import { useEffect, useId, useState } from "react";
import { PreviewUnavailable } from "./PreviewBoundary";

/**
 * A collapsible fixed-height card showing one server-rendered preview SVG.
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

export function SvgPreview({ src, title, heightClass = "h-64" }: Props) {
  const [expanded, setExpanded] = useState(true);
  const [state, setState] = useState<LoadState>("loading");
  const regionId = useId();

  // A new src is a new render — reset to the loading state so the spinner
  // shows again and a previous failure doesn't stick to the new image.
  useEffect(() => {
    setState("loading");
  }, [src]);

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
        <div
          id={regionId}
          className={`${heightClass} relative overflow-hidden rounded border bg-white`}
        >
          {state !== "failed" && (
            <img
              key={src}
              src={src}
              alt={`${title} rendered by KiCad`}
              className="h-full w-full object-contain"
              onLoad={() => setState("ready")}
              onError={() => setState("failed")}
              data-testid="svg-preview-img"
            />
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
