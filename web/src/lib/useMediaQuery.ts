import { useEffect, useState } from "react";

/**
 * Viewport media queries, in TypeScript.
 *
 * Almost every responsive decision in this app is made in CSS with a
 * Tailwind `sm:` / `lg:` prefix, and that is still the right default —
 * CSS costs nothing, never desyncs from the class it sits next to, and
 * survives a resize without a re-render.
 *
 * This hook exists for the narrow case where the *behaviour*, not just
 * the styling, forks on width: the parts list preview pane. Below `xl`
 * there is no room for a split pane, so a row click has to navigate to
 * the full part page exactly as it always has; at `xl` and up the same
 * click selects into the pane instead. A `hidden xl:flex` pane alone
 * would still leave the click handler selecting a row the user cannot
 * see, so the click handler has to know the breakpoint too.
 *
 * When you use this, keep the query and the Tailwind prefix in sync —
 * `XL_VIEWPORT_QUERY` is the one and only place that mapping is written
 * down.
 */

/**
 * Tailwind's `xl` breakpoint. `tailwind.config.js` extends only `colors`
 * and `fontFamily`, so the default screens apply and `xl` is 1280px.
 *
 * **Why `xl` and not `lg`.** The parts list is three columns now: the
 * category rail (#909) is `hidden lg:block w-56`, the table, and the
 * preview. Counting the 240px app sidebar and the page padding, a 320px
 * pane at `lg` would leave the table 176px — unusable. At `xl` it gets
 * 432px, at a 1440px laptop 592px, and the pane only widens to `w-96` at
 * `2xl`, where there is 688px to spare. So the rail appears at `lg` and
 * the preview waits for `xl`; between the two you get rail + table, which
 * is the layout #909 shipped.
 */
export const XL_VIEWPORT_QUERY = "(min-width: 1280px)";

function evaluate(query: string): boolean {
  // SSR has no window; jsdom has `matchMedia` only in newer versions and
  // always reports `matches: false`. Both should read as "narrow", which
  // is the behaviour that existed before any pane did.
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia(query).matches;
}

/** `true` while the viewport matches `query`. Re-renders on change. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => evaluate(query));

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return undefined;
    const mql = window.matchMedia(query);
    // Re-read on subscribe: the query may have changed, or the viewport
    // may have moved between the initial render and this effect.
    setMatches(mql.matches);
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches);
    // Safari < 14 and a few embedded webviews only ship the deprecated
    // addListener/removeListener pair, and `addEventListener` is simply
    // absent there rather than a no-op.
    if (typeof mql.addEventListener === "function") {
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    }
    mql.addListener(onChange);
    return () => mql.removeListener(onChange);
  }, [query]);

  return matches;
}

/** `true` at Tailwind's `xl` breakpoint and wider. */
export function useIsXlViewport(): boolean {
  return useMediaQuery(XL_VIEWPORT_QUERY);
}
