import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "fs";
import { join } from "path";

/**
 * Guards the typographic scale introduced alongside this test.
 *
 * The bug it exists to prevent: a "md" size step was used on 64 section
 * headings, but Tailwind's default fontSize scale has no such step and
 * `web/tailwind.config.js` extends only `colors` and `fontFamily`. The class
 * therefore compiled to nothing, and because preflight flattens h1-h6 to
 * `font-size: inherit`, every section title in the app rendered at body size.
 * A dead class is invisible in review — hence a test.
 *
 * Step names are listed bare (not as `text-` prefixed classes) so this file
 * does not trip its own scan.
 */

const srcDir = join(__dirname, "..");

/** Recursively collect all *.tsx / *.ts files under a directory. */
function findSourceFiles(dir: string): string[] {
  const results: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      results.push(...findSourceFiles(full));
    } else if (entry.endsWith(".tsx") || entry.endsWith(".ts")) {
      results.push(full);
    }
  }
  return results;
}

/**
 * Tailwind's default fontSize steps. This config never extends `fontSize`,
 * so these are the only `text-<step>` classes that produce any CSS.
 */
const REAL_STEPS = new Set([
  "xs", "sm", "base", "lg", "xl",
  "2xl", "3xl", "4xl", "5xl", "6xl", "7xl", "8xl", "9xl",
]);

/**
 * Words that read like a size step to a developer. Any of these that is not
 * in REAL_STEPS silently renders at the inherited size instead of failing
 * loudly — "md" was the one that actually shipped.
 */
const SIZE_LIKE = new Set([
  ...REAL_STEPS,
  "md", "xxs", "xxl", "xxxl",
  "tiny", "small", "medium", "normal", "regular", "large", "huge",
]);

describe("typographic scale", () => {
  it("index.css defines the three heading utilities", () => {
    const css = readFileSync(join(srcDir, "index.css"), "utf-8");
    for (const utility of [".page-title", ".card-title", ".section-title"]) {
      expect(css, `${utility} must be defined in index.css`).toContain(`${utility} {`);
    }
  });

  it("no source file uses a text-<step> class outside Tailwind's real fontSize scale", () => {
    const violations: string[] = [];

    for (const file of findSourceFiles(srcDir)) {
      const lines = readFileSync(file, "utf-8").split("\n");
      lines.forEach((line, idx) => {
        for (const match of line.matchAll(/\btext-([a-z0-9]+)\b/g)) {
          const step = match[1];
          if (SIZE_LIKE.has(step) && !REAL_STEPS.has(step)) {
            violations.push(`${file}:${idx + 1}: text-${step} — ${line.trim()}`);
          }
        }
      });
    }

    expect(
      violations,
      "These classes compile to nothing and render at the inherited size.\n" +
        "Use .page-title / .card-title / .section-title, or a real step:\n" +
        violations.join("\n"),
    ).toHaveLength(0);
  });

  it("the heading utilities are actually applied", () => {
    const sources = findSourceFiles(srcDir)
      .filter((f) => f.endsWith(".tsx"))
      .map((f) => readFileSync(f, "utf-8"));

    const uses = (utility: string) =>
      sources.filter((s) => new RegExp(`className="[^"]*\\b${utility}\\b`).test(s)).length;

    expect(uses("page-title"), "page-title should be the class on route titles").toBeGreaterThan(0);
    expect(uses("card-title"), "card-title should be the class on section titles").toBeGreaterThan(0);
  });
});
