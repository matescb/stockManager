import { useEffect, useId, useRef, useState } from "react";
import { loadKicanvas } from "./kicanvas";
import { PreviewBoundary, PreviewUnavailable } from "./PreviewBoundary";

/**
 * A collapsible fixed-height card holding one `<kicanvas-embed>`.
 *
 * `SymbolPreview` and `FootprintPreview` are this component with a URL;
 * everything that is awkward about embedding an alpha web component
 * lives here.
 *
 * The element is created imperatively rather than written as JSX. Two
 * reasons, both practical: `<kicanvas-embed>` is not a known intrinsic
 * element so JSX would need a global type declaration for a single call
 * site, and — more importantly — the element does substantial work on
 * connect (WebGL context, document parse, layout). Letting React reuse
 * one instance across `src` changes would hand it a viewer already
 * holding another document; recreating it per URL guarantees a clean
 * mount. `replaceChildren` on teardown is what disconnects it.
 *
 * Mounted only while expanded, for the same reason: a canvas sized zero
 * is a viewer that has to be told to re-measure, and not creating it is
 * simpler than fixing it up afterwards.
 */

type Props = {
  /** URL of a document KiCanvas can read — see `domain/eda/preview.py`. */
  src: string;
  /** Card heading. */
  title: string;
  /** Height class for the viewer area. */
  heightClass?: string;
};

type LoadState = "loading" | "ready" | "failed";

export function KicanvasFrame({ src, title, heightClass = "h-64" }: Props) {
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
          {/* Keyed on src so a new document gets a fresh boundary and a
              fresh viewer rather than a reset of the old one. */}
          <PreviewBoundary key={src} resetKey={src}>
            <Viewer src={src} />
          </PreviewBoundary>
        </div>
      )}
    </section>
  );
}

function Viewer({ src }: { src: string }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<LoadState>("loading");

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    let cancelled = false;
    setState("loading");

    loadKicanvas().then(
      () => {
        if (cancelled || !hostRef.current) return;
        const embed = document.createElement("kicanvas-embed");
        embed.setAttribute("src", src);
        // `basic` = pan/zoom/select without the sidebars, which are
        // meant for whole projects and have nothing to show for one
        // symbol. `nooverlay` drops the "click to interact" scrim;
        // `nodownload` drops a save button that would hand the user the
        // synthetic wrapper document rather than the file they uploaded.
        //
        // `noflipview` is load-bearing, not taste. The download and flip
        // buttons are the only two controls KiCanvas draws with Material
        // Symbols ligature text, and the vendored bundle is patched to
        // drop its Google Fonts <link> (CSP, and a beacon to Google on
        // every preview). Without that font a ligature icon renders as
        // the literal word "flip". Suppressing both buttons is what makes
        // the patch invisible: everything still on screen — the bottom
        // toolbar's zoom controls — uses the bundle's own SVG sprite.
        // Every other controlslist value is unimplemented at the pinned
        // commit. See docs/frontend/kicanvas-provenance.md.
        embed.setAttribute("controls", "basic");
        embed.setAttribute("controlslist", "nooverlay nodownload noflipview");
        embed.style.width = "100%";
        embed.style.height = "100%";
        hostRef.current.replaceChildren(embed);
        setState("ready");
      },
      () => {
        if (!cancelled) setState("failed");
      },
    );

    return () => {
      cancelled = true;
      // Disconnects the element, which is how the viewer releases its
      // WebGL context. Leaving it attached leaks one per remount.
      host.replaceChildren();
    };
  }, [src]);

  if (state === "failed") return <PreviewUnavailable />;

  return (
    <div className="relative h-full w-full">
      <div ref={hostRef} className="h-full w-full" data-testid="kicanvas-host" />
      {state === "loading" && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-muted">
          Loading preview…
        </div>
      )}
    </div>
  );
}
