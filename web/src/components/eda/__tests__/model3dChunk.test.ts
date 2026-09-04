/**
 * three.js is a ~600 KB dependency the CAD tab needs only when a 3D model
 * is actually opened. It earns its keep only if it stays out of the main
 * bundle — so this is the source-level guard that keeps the lazy boundary
 * intact.
 *
 * Two invariants, either of which silently pulls three.js back into the
 * eager graph if broken:
 *
 *  1. `three` (and its `three/examples/jsm/*` loaders) is imported at
 *     runtime in exactly one module — `modelRenderer.ts` — which nothing
 *     imports statically, so Rollup splits it into its own chunk.
 *  2. `ModelPreview.tsx` reaches that module through a dynamic
 *     `import("./modelRenderer")`. A plain (non-type) static import there
 *     would merge the three.js chunk straight back into `ModelPreview`'s.
 *
 * The final proof that the split actually happened lives in the build
 * output, not here (a unit test can't see Rollup's chunking); this catches
 * the regression at its source, cheaply, on every run.
 */
import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const RENDERER = path.join(SRC, "components/eda/modelRenderer.ts");
const MODEL_PREVIEW = path.join(SRC, "components/eda/ModelPreview.tsx");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules") continue;
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.(ts|tsx)$/.test(entry)) out.push(full);
  }
  return out;
}

// A runtime import (value import) from three or a three subpath. `import
// type ... from "three"` is erased by the compiler and does not bundle
// anything, so it is deliberately not matched.
const RUNTIME_THREE_IMPORT = /^\s*import\s+(?!type\b)[^;]*\bfrom\s+["']three(\/[^"']*)?["']/m;

describe("the three.js lazy chunk", () => {
  it("imports three only in modelRenderer.ts", () => {
    const offenders = walk(SRC).filter((file) => {
      if (file === RENDERER) return false;
      return RUNTIME_THREE_IMPORT.test(readFileSync(file, "utf-8"));
    });
    expect(
      offenders,
      `three.js must be imported only in modelRenderer.ts (lazy chunk). Offenders:\n${offenders.join("\n")}`,
    ).toEqual([]);
  });

  it("loads modelRenderer through a dynamic import in ModelPreview", () => {
    const source = readFileSync(MODEL_PREVIEW, "utf-8");
    expect(source).toMatch(/import\(\s*["']\.\/modelRenderer["']\s*\)/);
    // A non-type static import would defeat the split.
    expect(source).not.toMatch(
      /^\s*import\s+(?!type\b)[^;]*\bfrom\s+["']\.\/modelRenderer["']/m,
    );
  });
});
