/**
 * Contract test: KiCanvas can actually render what the backend builds.
 *
 * Runs on the default **node** environment, not jsdom — there is no DOM
 * here and no `<kicanvas-embed>`. What is under test is KiCanvas's
 * document layer: the real `KicadSch` and `KicadPCB` parsers, built from
 * the same upstream commit as the bundle the app ships, imported from
 * `web/test-vendor/kicanvas-parsers/` (its README explains why the shipped
 * bundle can't be used — it exports nothing).
 *
 * The documents are the exact bytes `backend/app/domain/eda/preview.py`
 * emits, checked in at `backend/tests/fixtures/eda/preview/` and kept in
 * sync by `backend/tests/test_eda_preview_fixtures.py`. Reading across the
 * tree is deliberate: these files ARE the contract, and giving the
 * frontend its own copy would let the two drift silently.
 *
 * Why this exists at all: a preview KiCanvas cannot parse renders **blank**
 * — no exception, no console error, nothing in Sentry. Every assertion
 * below therefore checks that real geometry survived, not merely that a
 * parse returned an object.
 *
 * When this fails after a KiCanvas bump, the preview is broken in
 * production; do not update the fixtures to match the new parser without
 * checking a preview actually draws.
 */
import { describe, it, expect, beforeAll } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import {
  KicadSch,
  KicadPCB,
  type LibSymbol,
} from "../../../../test-vendor/kicanvas-parsers/index.mjs";

const FIXTURES = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../../../backend/tests/fixtures/eda/preview",
);

function read(name: string): string {
  return readFileSync(path.join(FIXTURES, name), "utf-8");
}

beforeAll(() => {
  // KiCanvas's logger calls `console.info.bind(window.console)` while
  // parsing. A browser library running headless needs the global; this is
  // a shim, not a patch to the library.
  (globalThis as { window?: unknown }).window ??= { console };
});

// The pad-layer wildcards KiCanvas expands to both sides
// (`viewers/board/painter.ts::PadPainter.layers_for`).
function expandLayer(layer: string): string[] {
  return layer.startsWith("*.")
    ? [`F.${layer.slice(2)}`, `B.${layer.slice(2)}`]
    : [layer];
}

describe("symbol previews parse as schematics KiCanvas can draw", () => {
  const doc = () => new KicadSch("symbol_R.kicad_sch", read("symbol_R.kicad_sch"));

  it("carries the stored entry in lib_symbols", () => {
    const entries = [...(doc().lib_symbols?.symbols.values() ?? [])];
    expect(entries.map((e) => e.name)).toEqual(["R"]);
  });

  it("keeps the geometry that makes the symbol non-blank", () => {
    const entry = [...doc().lib_symbols!.symbols.values()][0] as LibSymbol;

    // A KiCad symbol's visible content lives in its unit sub-symbols:
    // `R_0_1` holds the body rectangle, `R_1_1` holds the pins. If the
    // wrapper re-emitted the entry instead of embedding it verbatim,
    // this is what would quietly disappear.
    const units = Object.fromEntries(
      entry.children.map((c) => [
        c.name,
        { pins: c.pins.length, drawings: c.drawings.length },
      ]),
    );
    expect(units).toEqual({
      R_0_1: { pins: 0, drawings: 1 },
      R_1_1: { pins: 2, drawings: 0 },
    });
    expect(entry.properties.size).toBeGreaterThan(0);
  });

  it("resolves the placement to that entry", () => {
    // The constraint that decides blank-vs-drawn: KiCanvas looks `lib_id`
    // up in lib_symbols BY NAME, with no library-nickname handling and no
    // fallback. `lib_symbol` coming back undefined is exactly the silent
    // failure this whole test file exists to catch.
    const placement = [...doc().symbols.values()][0];
    expect(placement.lib_id).toBe("R");
    expect(placement.lib_symbol?.name).toBe("R");
  });

  it("gives the placement the Value property KiCanvas dereferences", () => {
    // Without one, KiCanvas reads `this.default_instance.value` on an
    // undefined `default_instance` and throws DURING PARSE, taking the
    // whole document with it — not just this symbol. Parsing at all is
    // most of the assertion; the value is the rest.
    expect(doc().symbols.size).toBe(1);
    expect(
      [...doc().symbols.values()][0].get_property_text("Value"),
    ).toBe("R");
  });
});

describe("footprint previews parse as boards KiCanvas can draw", () => {
  const cases = [
    {
      file: "footprint_front_smd.kicad_pcb",
      name: "R_0402_1005Metric",
      pads: 2,
      padLayers: ["F.Cu", "F.Paste", "F.Mask"],
    },
    {
      file: "footprint_back_and_through_hole.kicad_pcb",
      name: "SOIC-8_BackSide",
      pads: 3,
      padLayers: ["B.Cu", "B.Paste", "B.Mask"],
    },
  ];

  it.each(cases)("$file yields its pads and drawings", (c) => {
    const board = new KicadPCB(c.file, read(c.file));

    expect(board.footprints).toHaveLength(1);
    const fp = board.footprints[0];
    expect(fp.library_link).toBe(c.name);
    expect(fp.pads).toHaveLength(c.pads);
    expect(fp.drawings.length).toBeGreaterThan(0);
    expect(fp.pads[0].layers).toEqual(c.padLayers);
  });

  it("keeps a back-side footprint on the back copper layer", () => {
    // The B.Cu case the front-side fixture cannot cover: back-side pads
    // render through a different KiCanvas layer, and a board that never
    // declared B.Cu would hide them.
    const board = new KicadPCB(
      "footprint_back_and_through_hole.kicad_pcb",
      read("footprint_back_and_through_hole.kicad_pcb"),
    );
    const fp = board.footprints[0];

    expect(fp.layer).toBe("B.Cu");
    const smd = fp.pads.filter((p) => p.type === "smd");
    expect(smd.length).toBeGreaterThan(0);
    for (const pad of smd) expect(pad.layers).toContain("B.Cu");
  });

  it("keeps a through-hole pad on the both-sides wildcard", () => {
    const board = new KicadPCB(
      "footprint_back_and_through_hole.kicad_pcb",
      read("footprint_back_and_through_hole.kicad_pcb"),
    );
    const tht = board.footprints[0].pads.find((p) => p.type === "thru_hole");

    expect(tht).toBeDefined();
    expect(tht!.layers).toContain("*.Cu");
  });

  it.each(cases)(
    "$file only draws on layers the synthetic board declares",
    (c) => {
      // KiCanvas builds its render layers by intersecting a fixed list
      // with the layers the BOARD declares
      // (`viewers/board/layers.ts::LayerSet`), so anything the wrapper
      // forgot to declare has nowhere to draw. Copper is worse than
      // invisible: pad visibility dereferences the F.Cu / B.Cu layers with
      // a non-null assertion and throws when they are absent.
      const board = new KicadPCB(c.file, read(c.file));
      const declared = new Set(board.layers.map((l) => l.canonical_name));
      const fp = board.footprints[0];

      const referenced = new Set(fp.pads.flatMap((p) => p.layers));
      // Graphics carry a single layer each; read them off the raw text
      // rather than the model so a parser change can't hide one.
      for (const match of read(c.file).matchAll(/\(layer "([^"]+)"\)/g)) {
        referenced.add(match[1]);
      }

      const missing = [...referenced]
        .flatMap(expandLayer)
        .filter((name) => !declared.has(name));
      expect(missing).toEqual([]);
    },
  );

  it("declares the full standard two-layer stack", () => {
    // Belt and braces for footprints no fixture covers: these are the
    // layers a real KiCad footprint may use without being exotic.
    const board = new KicadPCB(
      "footprint_front_smd.kicad_pcb",
      read("footprint_front_smd.kicad_pcb"),
    );
    const declared = new Set(board.layers.map((l) => l.canonical_name));

    for (const side of ["F", "B"]) {
      for (const kind of ["Cu", "Mask", "Paste", "SilkS", "CrtYd", "Fab"]) {
        expect(declared).toContain(`${side}.${kind}`);
      }
    }
    expect(declared).toContain("Edge.Cuts");
  });
});
