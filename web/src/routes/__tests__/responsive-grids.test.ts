import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "fs";
import { join } from "path";

/** Recursively collect all *.tsx files under a directory. */
function findTsxFiles(dir: string): string[] {
  const results: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      results.push(...findTsxFiles(full));
    } else if (entry.endsWith(".tsx")) {
      results.push(full);
    }
  }
  return results;
}

/**
 * Matches a bare `grid grid-cols-2` that is NOT preceded by a responsive
 * breakpoint prefix (sm:, md:, lg:, xl:, 2xl:).
 *
 * We need to detect strings like:
 *   "grid grid-cols-2"     ← bare — bad
 * but NOT:
 *   "sm:grid-cols-2"       ← prefixed — fine
 *   "lg:grid-cols-2"       ← prefixed — fine
 */
const BARE_GRID_COLS_2 = /(?<![a-z\d]:)grid grid-cols-2(?!\s*\w)/g;

describe("responsive-grids", () => {
  it("no TSX file contains a bare 'grid grid-cols-2' without a responsive prefix", () => {
    const srcDir = join(__dirname, "../..");
    const files = findTsxFiles(srcDir);

    const violations: string[] = [];

    for (const file of files) {
      const content = readFileSync(file, "utf-8");
      const lines = content.split("\n");
      lines.forEach((line, idx) => {
        // Match bare `grid grid-cols-2` not preceded by a breakpoint prefix on same word
        if (/\bgrid grid-cols-2\b/.test(line) && !/\bsm:grid-cols-2\b|\bmd:grid-cols-2\b|\blg:grid-cols-2\b|\bxl:grid-cols-2\b|\b2xl:grid-cols-2\b/.test(line)) {
          violations.push(`${file}:${idx + 1}: ${line.trim()}`);
        }
      });
    }

    expect(violations, `Bare grid-cols-2 found (fix with grid-cols-1 sm:grid-cols-2):\n${violations.join("\n")}`).toHaveLength(0);
  });
});
