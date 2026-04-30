/**
 * Parser test fixtures pulled from real bag-scan logs we've seen in
 * production. The point isn't to over-cover the parser's branches but to
 * pin down the hard cases that already burned us in the field — every
 * test here corresponds to a regression that hit users.
 */
import { describe, it, expect } from "vitest";
import { parseBagCode, bagLotName, bagComments, bagSignature } from "./bagCode";

describe("parseBagCode — separator handling", () => {
  it("splits on real ASCII control separators (Scandit-shaped input)", () => {
    // Mouser bag with raw \x1d separators, header with \x1e.
    const raw =
      "[)>\x1e06\x1d1P98266-0897\x1dQ3\x1d1KPO12345\x1d10D2545\x1e\x04";
    const b = parseBagCode(raw);
    expect(b.mpn).toBe("98266-0897");
    expect(b.quantity).toBe(3);
    expect(b.poNumber).toBe("PO12345");
    expect(b.dateCode).toBe("2545");
  });

  it("normalises Unicode 'Control Pictures' (ZXing-shaped input)", () => {
    // Real ZXing-wasm output for the same Mouser bag — control bytes
    // come through as printable U+241D / U+241E / U+2404 instead of raw.
    // Without normalisation, the trailing GS (␝) lands in the value
    // and the MPN reads "98266-0897␝" — that's what 500'd lookup-mpn.
    const raw =
      "[)>␞06␝K#44861␠A␠#44920␝14K017␝1P98266-0897␝Q3␝11K078101306␝4LHU␝1VMolex␞␄";
    const b = parseBagCode(raw);
    expect(b.mpn).toBe("98266-0897");
    expect(b.quantity).toBe(3);
    expect(b.lineItem).toBe("017");
    expect(b.invoiceRef).toBe("078101306");
    expect(b.manufacturer).toBe("Molex");
  });

  it("decodes Symbol-for-Space (U+2420) inside field values", () => {
    // The customer-reference field can carry literal spaces. ZXing
    // substitutes those for U+2420; without normalisation the parsed
    // string keeps the pictograph and looks broken in the UI.
    const raw =
      "[)>␞06␝K#44861␠A␠#44920␝1PFOO␞␄";
    expect(parseBagCode(raw).customerRef).toBe("#44861 A #44920");
  });

  it("treats a plain MPN with no DI and no header as the MPN itself", () => {
    // 1D Code128 case. No "[)>" header, no DI prefixes match, so the
    // input IS the MPN.
    const b = parseBagCode("RC0402JR-070R");
    expect(b.mpn).toBe("RC0402JR-070R");
    expect(b.quantity).toBeUndefined();
  });

  it("does not split inside customer-reference '#' values", () => {
    // Mouser's K field commonly contains '#' as legitimate content
    // ("K#44861 A #44920"). An earlier version of the parser treated '#'
    // as a separator substitute and tore the value apart. Regression
    // test — '#' must NOT split.
    const raw =
      "[)>␞06␝K#44861 A #44920␝1PFOO␞␄";
    const b = parseBagCode(raw);
    expect(b.customerRef).toBe("#44861 A #44920");
    expect(b.mpn).toBe("FOO");
  });
});

describe("parseBagCode — field assignments", () => {
  it("preserves traceability fields the import flow uses", () => {
    const raw =
      "[)>\x1e06\x1d1PRC0402\x1dQ50\x1d1T LOT12345\x1d10D2545\x1d1KPO44861\x1d11K078101306\x1d14K017\x1e\x04";
    const b = parseBagCode(raw);
    expect(b.lotBatch).toBe("LOT12345");
    expect(b.dateCode).toBe("2545");
    expect(b.poNumber).toBe("PO44861");
    expect(b.invoiceRef).toBe("078101306");
    expect(b.lineItem).toBe("017");
  });

  it("returns empty mpn for empty input", () => {
    expect(parseBagCode("").mpn).toBe("");
  });

  it("rejects non-positive quantity values", () => {
    const raw = "[)>\x1e06\x1d1PFOO\x1dQ0\x1e\x04";
    expect(parseBagCode(raw).quantity).toBeUndefined();
  });
});

describe("bagSignature", () => {
  it("hashes the same bag identically across decoder pictogram differences", async () => {
    // Same physical bag, two decoders. Scandit emits raw control chars;
    // ZXing emits the Unicode "Symbol for X" pictograms. Both should
    // hash to the same signature so dedup works regardless of decoder.
    const scandit =
      "[)>\x1e06\x1d1P98266-0897\x1dQ3\x1dK#44861 A #44920\x1e\x04";
    const zxing =
      "[)>␞06␝1P98266-0897␝Q3␝K#44861␠A␠#44920␞␄";
    const a = await bagSignature(scandit);
    const b = await bagSignature(zxing);
    expect(a).toBeTruthy();
    expect(a).toBe(b);
  });

  it("different bags hash to different signatures", async () => {
    const a = await bagSignature("[)>\x1e06\x1d1PFOO-1\x1eEOT");
    const b = await bagSignature("[)>\x1e06\x1d1PFOO-2\x1eEOT");
    expect(a).not.toBe(b);
  });

  it("returns null for empty input", async () => {
    expect(await bagSignature("")).toBeNull();
    expect(await bagSignature("   ")).toBeNull();
  });
});

describe("bagLotName / bagComments helpers", () => {
  it("synthesises a lot name from 1T + 10D, when both present", () => {
    const b = parseBagCode("[)>\x1e06\x1d1PFOO\x1d1TLOT99\x1d10D2545\x1e\x04");
    expect(bagLotName(b)).toBe("Lot LOT99 · DC 2545");
  });

  it("returns null when no traceability fields are present", () => {
    expect(bagLotName({ mpn: "X", raw: "X" })).toBeNull();
    expect(bagComments({ mpn: "X", raw: "X" })).toBeNull();
  });

  it("composes the comment from K + 1K + 14K + 11K when present", () => {
    const b = parseBagCode(
      "[)>␞06␝KCUST␝1KPO1␝14K017␝11KINV9␝1PFOO␞␄"
    );
    expect(bagComments(b)).toBe("Order CUST · PO PO1 · line 017 · invoice INV9");
  });
});
