/**
 * Unit tests for the label designer's mm geometry.
 *
 * Ported from the sibling skladVA project
 * (/mnt/data/WORK/sklad, `frontend/src/routes/labels/geometry.test.ts`) — the
 * mm/px, snap, clamp and binding-resolution suites are its cases, retargeted
 * at this codebase's binding set. The QR-sizing suite is new: skladVA guessed
 * "a QR is about 25 modules", we derive the version from the ISO 18004 byte
 * capacity table, which is what makes the canvas footprint match the print.
 *
 * The binding-precedence cases are the load-bearing ones. They pin the
 * designer to `backend/app/domain/printing/label_render.py`:
 *   `_resolve_text`  literal `text` wins; `binding` only when text is ABSENT
 *   `_qr_payload`    literal, else binding, else `{{url}}`
 * Getting that backwards prints blank labels.
 */
import { describe, it, expect } from "vitest";
import {
  SAMPLE_CODE,
  bindingToken,
  clampToLabel,
  elementFootprint,
  interpolate,
  mmToPx,
  previewSanitize,
  pxToMm,
  qrModuleCount,
  qrModulesForVersion,
  qrSideMm,
  qrVersionFor,
  resolveBinding,
  resolveQrPayload,
  resolveTextValue,
  sampleContext,
  snapMm,
  snapPoint,
  type SampleContext,
} from "../geometry";
import type { LabelElement } from "../types";

const CTX: SampleContext = {
  code: "ABCD1234",
  url: "https://parts.example.com/c/ABCD1234",
  name: "Widget",
  mpn: "SMPL-1234",
  manufacturer: "Sample Mfr",
};

describe("mm <-> px", () => {
  it("converts mm to px at the given zoom", () => {
    expect(mmToPx(10, 4)).toBe(40);
  });

  it("round-trips px back to mm", () => {
    expect(pxToMm(mmToPx(7.5, 4), 4)).toBeCloseTo(7.5);
  });
});

describe("snapMm", () => {
  it("snaps to the nearest grid line", () => {
    expect(snapMm(2.4, 1)).toBe(2);
    expect(snapMm(2.6, 1)).toBe(3);
  });

  it("snaps to a half-mm grid", () => {
    expect(snapMm(2.3, 0.5)).toBe(2.5);
  });

  it("keeps sub-mm precision when the grid is disabled", () => {
    expect(snapMm(2.345, 0)).toBe(2.35);
  });

  it("returns 0 for non-finite input", () => {
    expect(snapMm(Number.NaN, 1)).toBe(0);
    expect(snapMm(Number.POSITIVE_INFINITY, 1)).toBe(0);
  });
});

describe("snapPoint", () => {
  it("snaps both axes and returns a new object", () => {
    const input = { x: 1.2, y: 3.8 };
    const out = snapPoint(input, 1);
    expect(out).toEqual({ x: 1, y: 4 });
    expect(out).not.toBe(input);
  });
});

describe("clampToLabel", () => {
  const label = { width_mm: 50, height_mm: 30 };

  it("keeps a point inside the label given its footprint", () => {
    expect(clampToLabel({ x: 48, y: 28 }, { w: 6, h: 6 }, label)).toEqual({
      x: 44,
      y: 24,
    });
  });

  it("clamps negatives to zero", () => {
    expect(clampToLabel({ x: -5, y: -5 }, { w: 6, h: 6 }, label)).toEqual({
      x: 0,
      y: 0,
    });
  });

  it("clamps to the origin when the element is larger than the label", () => {
    expect(clampToLabel({ x: 10, y: 10 }, { w: 60, h: 40 }, label)).toEqual({
      x: 0,
      y: 0,
    });
  });
});

describe("QR sizing", () => {
  it("maps a version to its module count", () => {
    expect(qrModulesForVersion(1)).toBe(21);
    expect(qrModulesForVersion(2)).toBe(25);
    expect(qrModulesForVersion(40)).toBe(177);
  });

  it("clamps an out-of-range version rather than throwing", () => {
    expect(qrModulesForVersion(0)).toBe(21);
    expect(qrModulesForVersion(99)).toBe(177);
  });

  it("picks the smallest version that holds the payload, per EC level", () => {
    // ISO 18004 table 7, byte mode, version 1 capacities.
    expect(qrVersionFor(17, "L")).toBe(1);
    expect(qrVersionFor(18, "L")).toBe(2);
    expect(qrVersionFor(14, "M")).toBe(1);
    expect(qrVersionFor(15, "M")).toBe(2);
    expect(qrVersionFor(11, "Q")).toBe(1);
    expect(qrVersionFor(7, "H")).toBe(1);
    expect(qrVersionFor(8, "H")).toBe(2);
  });

  it("degrades to version 40 instead of throwing on an overflowing payload", () => {
    expect(qrVersionFor(100000, "H")).toBe(40);
  });

  it("counts payload bytes, not characters", () => {
    // 13 ASCII characters are 13 bytes, which fits version 1 at EC M (14)...
    expect(qrModuleCount("x".repeat(13), "M")).toBe(21);
    // ...but the same 13 characters in a two-byte encoding are 26 bytes,
    // which needs version 2 (26) and therefore a bigger symbol.
    expect(qrModuleCount("é".repeat(13), "M")).toBe(25);
  });

  it("derives the printed side length from module count x module size", () => {
    // 8 bytes at EC M is version 1 (21 modules); 21 * 0.5 mm = 10.5 mm.
    expect(qrSideMm("12345678", "M", 0.5)).toBeCloseTo(10.5);
  });

  it("falls back to a sane module size for a non-positive dotsize", () => {
    expect(qrSideMm("12345678", "M", 0)).toBeCloseTo(10.5);
  });
});

describe("elementFootprint", () => {
  it("sizes a QR from its real module count", () => {
    const el: LabelElement = {
      id: "a",
      kind: "qr",
      x_mm: 0,
      y_mm: 0,
      rotation: 0,
      dotsize_mm: 0.5,
      ec: "M",
      binding: "url",
    };
    expect(elementFootprint(el, "12345678").w).toBeCloseTo(10.5);
    expect(elementFootprint(el, "12345678").h).toBeCloseTo(10.5);
  });

  it("uses the rule length and thickness for a handwriting line", () => {
    const el: LabelElement = {
      id: "b",
      kind: "handwriting",
      x_mm: 0,
      y_mm: 0,
      rotation: 0,
      w_mm: 40,
      h_mm: 0.3,
    };
    expect(elementFootprint(el)).toEqual({ w: 40, h: 0.3 });
  });

  it("grows a text footprint with the point size", () => {
    const base = {
      id: "c",
      kind: "text",
      x_mm: 0,
      y_mm: 0,
      rotation: 0,
      font: 3,
    } as const;
    const small = elementFootprint({ ...base, size_pt: 8 }, "Hello");
    const large = elementFootprint({ ...base, size_pt: 16 }, "Hello");
    expect(large.w).toBeGreaterThan(small.w);
    expect(large.h).toBeGreaterThan(small.h);
  });
});

describe("bindingToken", () => {
  it("strips the brace wrapper, with or without whitespace", () => {
    expect(bindingToken("{{code}}")).toBe("code");
    expect(bindingToken("{{ code }}")).toBe("code");
    expect(bindingToken("code")).toBe("code");
  });
});

describe("resolveBinding", () => {
  it("resolves a known token with or without braces", () => {
    expect(resolveBinding("{{code}}", CTX)).toBe("ABCD1234");
    expect(resolveBinding("name", CTX)).toBe("Widget");
    expect(resolveBinding("mpn", CTX)).toBe("SMPL-1234");
  });

  it("resolves an unknown token to an empty string, as the renderer does", () => {
    expect(resolveBinding("{{nope}}", CTX)).toBe("");
  });
});

describe("interpolate", () => {
  it("substitutes embedded tokens in literal text", () => {
    expect(interpolate("SN: {{code}} / {{name}}", CTX)).toBe(
      "SN: ABCD1234 / Widget",
    );
  });

  it("tolerates whitespace inside the braces", () => {
    expect(interpolate("{{ code }}", CTX)).toBe("ABCD1234");
  });

  it("leaves text without tokens unchanged", () => {
    expect(interpolate("PART", CTX)).toBe("PART");
  });

  it("blanks an unknown token rather than leaving it visible", () => {
    expect(interpolate("a{{sku}}b", CTX)).toBe("ab");
  });
});

describe("resolveTextValue", () => {
  it("prefers a LITERAL over a binding (matching label_render._resolve_text)", () => {
    expect(resolveTextValue({ text: "literal", binding: "code" }, CTX)).toBe(
      "literal",
    );
  });

  it("falls through to the binding only when text is absent", () => {
    expect(resolveTextValue({ binding: "code" }, CTX)).toBe("ABCD1234");
    expect(resolveTextValue({ text: "", binding: "code" }, CTX)).toBe("ABCD1234");
    expect(resolveTextValue({ text: null, binding: "code" }, CTX)).toBe("ABCD1234");
  });

  it("returns an empty string when neither is set", () => {
    expect(resolveTextValue({}, CTX)).toBe("");
  });
});

describe("resolveQrPayload", () => {
  it("defaults a bare QR to the scan-to-open url", () => {
    expect(resolveQrPayload({}, CTX)).toBe(CTX.url);
  });

  it("honours an explicit binding", () => {
    expect(resolveQrPayload({ binding: "code" }, CTX)).toBe("ABCD1234");
  });

  it("honours a literal payload over the binding", () => {
    expect(resolveQrPayload({ text: "raw", binding: "code" }, CTX)).toBe("raw");
  });
});

describe("previewSanitize", () => {
  it("replaces the JScript command separators with spaces", () => {
    expect(previewSanitize("widget\r\nA 500")).toBe("widget  A 500");
  });

  it("strips the field terminator", () => {
    expect(previewSanitize("a;b")).toBe("a b");
  });

  it("trims the result", () => {
    expect(previewSanitize("  hi  ")).toBe("hi");
  });
});

describe("sampleContext", () => {
  it("builds the /c/<code> scan url from the origin", () => {
    const ctx = sampleContext("part", { origin: "https://parts.example.com/" });
    expect(ctx.url).toBe(`https://parts.example.com/c/${SAMPLE_CODE}`);
    expect(ctx.code).toBe(SAMPLE_CODE);
  });

  it("names the entity type in the sample name", () => {
    expect(sampleContext("storage_location").name).toBe("Sample storage location");
    expect(sampleContext("storage_location").entity_type).toBe("storage_location");
  });

  it("declares every binding so no raw {{token}} reaches the label", () => {
    const ctx = sampleContext("build");
    for (const token of ["code", "url", "name", "description", "quantity", "project_name"]) {
      expect(typeof ctx[token]).toBe("string");
    }
  });
});
