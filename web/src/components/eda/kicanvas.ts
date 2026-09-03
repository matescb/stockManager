/**
 * Loads the vendored KiCanvas bundle, once, on demand.
 *
 * The bundle is a static asset (`web/public/kicanvas/`), not a module in
 * the bundle graph — see docs/frontend/kicanvas-provenance.md for why, and
 * for the pin. Importing it is a side effect: it registers the
 * `<kicanvas-embed>` and `<kicanvas-source>` custom elements and exports
 * nothing worth having. So "loading" means appending one module script
 * and waiting for it, and every caller after the first shares the same
 * promise — half a megabyte is not worth fetching twice, and defining a
 * custom element twice throws.
 *
 * Nothing here runs until a preview actually mounts, which is the point:
 * the CAD tab is the only place that needs KiCanvas, and most visits to
 * it never select a hosted entry.
 */

/** Resolves when `<kicanvas-embed>` is defined and usable. */
let pending: Promise<void> | null = null;

export const KICANVAS_SRC = `${import.meta.env.BASE_URL}kicanvas/kicanvas.js`;

export function loadKicanvas(): Promise<void> {
  if (pending) return pending;

  pending = new Promise<void>((resolve, reject) => {
    if (customElements.get("kicanvas-embed")) {
      resolve();
      return;
    }

    const script = document.createElement("script");
    script.type = "module";
    script.dataset.kicanvas = "";
    script.src = KICANVAS_SRC;
    script.addEventListener("load", () => resolve(), { once: true });
    script.addEventListener(
      "error",
      () => {
        // Drop the dead tag. A script element that has already failed
        // never fires again, so leaving it would make the retry below
        // hang forever instead of failing.
        script.remove();
        reject(new Error("KiCanvas failed to load"));
      },
      { once: true },
    );
    document.head.appendChild(script);
  });

  // A failed load must not poison every later attempt — drop the memo so
  // remounting the panel retries. `.catch` here also keeps a rejection
  // from surfacing as unhandled when no preview is mounted to observe it.
  pending.catch(() => {
    pending = null;
  });

  return pending;
}
