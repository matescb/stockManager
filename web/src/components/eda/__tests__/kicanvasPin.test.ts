/**
 * The KiCanvas pin is recorded in three places; this keeps them one place.
 *
 * There are two builds of KiCanvas in this repo — `public/kicanvas/`, which
 * the app loads, and `test-vendor/kicanvas-parsers/`, which
 * `kicanvasContract.test.ts` parses with. They must come from the same
 * upstream commit. If they ever diverge, the contract test would go on
 * passing against a parser the app no longer uses, and the preview would
 * render blank in production with every suite green — the exact outcome
 * that whole test pair exists to prevent.
 *
 * Nothing about a file's content forces its documentation to stay true, so
 * this asserts the shipped bundle still hashes to what PROVENANCE.md says
 * it does. Swapping the bundle without updating the docs — or updating the
 * docs without rebuilding the parsers — fails here.
 *
 * On a deliberate bump: rebuild both bundles from the new commit, then
 * update the commit and sha256 in `docs/frontend/kicanvas-provenance.md`
 * and `test-vendor/kicanvas-parsers/README.md`. Both carry the steps.
 *
 * The provenance page lives in `docs/`, not beside the bundle: everything
 * under `web/public/` is served verbatim to the browser, and that page
 * names backend paths and advertises the viewer as alpha. Only
 * `LICENSE.md` ships beside the code, because the MIT notice requires it.
 */
import { describe, it, expect } from "vitest";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const WEB = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../..",
);

const SHIPPED = path.join(WEB, "public/kicanvas/kicanvas.js");
const PROVENANCE = path.join(WEB, "../docs/frontend/kicanvas-provenance.md");
const PARSERS = path.join(WEB, "test-vendor/kicanvas-parsers/index.mjs");
const PARSERS_README = path.join(WEB, "test-vendor/kicanvas-parsers/README.md");

const COMMIT = "b031159eb74aaa7eef2b026fd85d35bc05ff2095";

// The bundle as shipped, i.e. AFTER the local patch documented in
// PROVENANCE.md. The pristine upstream build hashes to ca910f25… ; that
// value is recorded there too, so both are traceable.
const SHIPPED_SHA256 =
  "4b618fdd7beb0516bb3702b5af9dde370b2046e0171fa4e9ec73caa9bc216776";

function sha256(file: string): string {
  return createHash("sha256").update(readFileSync(file)).digest("hex");
}

describe("the vendored KiCanvas pin", () => {
  it("ships the bundle PROVENANCE.md describes", () => {
    const doc = readFileSync(PROVENANCE, "utf-8");
    expect(sha256(SHIPPED)).toBe(SHIPPED_SHA256);
    expect(doc).toContain(COMMIT);
    expect(doc).toContain(SHIPPED_SHA256);
  });

  it("keeps the Google Fonts injection patched out", () => {
    // Upstream appends a <link> to fonts.googleapis.com at module scope.
    // It is blocked by our CSP anyway, and it beacons every viewer's IP
    // and Referer — which carries the part-detail URL — to Google. A
    // rebuild that forgets to re-apply the patch would reintroduce both
    // silently, so assert on content rather than trusting the hash alone.
    const bundle = readFileSync(SHIPPED, "utf-8");
    expect(bundle).not.toContain("fonts.googleapis.com");
    expect(bundle).not.toContain("fonts.gstatic.com");
  });

  it("documents the pristine build it was patched from", () => {
    // Traceability: the patch is only auditable if the upstream artifact
    // it started from is named.
    expect(readFileSync(PROVENANCE, "utf-8")).toContain(
      "ca910f25276c3efb9aacb3a5d6341d4d9af4736d4c875fb0440d2cc856865ab7",
    );
  });

  it("tests against parsers built from the same commit", () => {
    const doc = readFileSync(PARSERS_README, "utf-8");
    expect(doc).toContain(COMMIT);
    expect(doc).toContain(sha256(PARSERS));
  });

  it("ships the licence but not the provenance page", () => {
    // Everything under public/ is served verbatim at a public URL. The
    // licence belongs there — MIT requires the notice to accompany the
    // distribution. The provenance page does not: it cites backend source
    // paths and states plainly that the viewer is alpha, which is
    // reconnaissance for anyone who asks for it.
    expect(existsSync(path.join(WEB, "public/kicanvas/LICENSE.md"))).toBe(true);
    expect(existsSync(path.join(WEB, "public/kicanvas/PROVENANCE.md"))).toBe(
      false,
    );
    expect(existsSync(PROVENANCE)).toBe(true);
  });

  it("keeps the shipped bundle out of the app's module graph", () => {
    // It is a static asset loaded by URL, and moving it under src/ would
    // put half a megabyte of minified alpha third-party code into the main
    // chunk — plus eslint and tsc. `components/eda/kicanvas.ts` is the only
    // thing that should name it, and only as a URL.
    const loader = readFileSync(
      path.join(WEB, "src/components/eda/kicanvas.ts"),
      "utf-8",
    );
    expect(loader).toContain("kicanvas/kicanvas.js");
    expect(loader).not.toMatch(/^\s*import\s.*kicanvas\.js/m);
  });
});
