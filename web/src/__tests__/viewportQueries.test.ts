import { describe, it, expect } from "vitest";
import resolveConfig from "tailwindcss/resolveConfig";

// @ts-expect-error -- tailwind.config.js is plain JS and `tsc -b` runs with
// allowJs off, so this import has no declarations. Reading the real config
// (rather than tailwindcss/defaultTheme) is the point: the guard has to
// notice a `screens` override being added.
import tailwindConfig from "../../tailwind.config.js";
import { LG_VIEWPORT_QUERY, XL_VIEWPORT_QUERY } from "@/lib/useMediaQuery";

/**
 * Guards the one place a Tailwind breakpoint is restated in TypeScript.
 *
 * `useMediaQuery.ts` exists because the parts list forks its *behaviour*,
 * not just its styling, on width: below the pane's breakpoint a row click
 * navigates; at and above it, the click selects into the pane. The pane's
 * `lg:flex` / `xl:flex` class and the hook's `(min-width: …px)` query are
 * two spellings of the same threshold, and only one of them is checked by
 * the compiler.
 *
 * The failure this prevents is silent in both directions. Move
 * `LG_VIEWPORT_QUERY` without touching `tailwind.config.js` and the click
 * handler starts selecting rows into a pane CSS is still hiding — the
 * exact bug `usePartPreview`'s comments warn about. Add a `screens`
 * override to the config without touching the constants and the same
 * thing happens from the other side. The DOM tests can't catch it: they
 * derive their viewport widths from these very constants, so they move
 * with the bug.
 */

const screens = resolveConfig(tailwindConfig).theme?.screens as
  | Record<string, string>
  | undefined;

/** `(min-width: 1024px)` — the query a Tailwind `lg:` prefix compiles to. */
function queryFor(screen: string): string {
  const width = screens?.[screen];
  if (typeof width !== "string") {
    throw new Error(`tailwind screen "${screen}" is not a plain min-width`);
  }
  return `(min-width: ${width})`;
}

describe("viewport queries mirror Tailwind's breakpoints", () => {
  it("LG_VIEWPORT_QUERY is Tailwind's lg", () => {
    expect(LG_VIEWPORT_QUERY).toBe(queryFor("lg"));
  });

  it("XL_VIEWPORT_QUERY is Tailwind's xl", () => {
    expect(XL_VIEWPORT_QUERY).toBe(queryFor("xl"));
  });

  it("the two are ordered, so `lg` really is the wider-reaching one", () => {
    const widthOf = (query: string) => Number(/(\d+)px/.exec(query)?.[1]);
    expect(widthOf(LG_VIEWPORT_QUERY)).toBeLessThan(widthOf(XL_VIEWPORT_QUERY));
  });
});
