/**
 * Parser test fixtures pulled from real bag-scan logs we've seen in
 * production. The point isn't to over-cover the parser's branches but to
 * pin down the hard cases that already burned us in the field — every
 * test here corresponds to a regression that hit users.
 */
import { describe, it, expect } from "vitest";
import { parseBagCode, bagLotName, bagComments, bagSignature, noMpnMessage } from "./bagCode";
import bagSignaturesFixture from "./__fixtures__/bagSignatures.json";

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

describe("parseBagCode — separators lost (keyboard-wedge reader)", () => {
  // Owner's Mouser bag as a USB wedge reader typed it: RS/GS dropped, so
  // every DI abuts the previous value. The label carries the customer
  // part number (P) but no 1P.
  const MOUSER_NO_1P =
    "[)>06K4048970914K027PC201,C202,C503Q10011K0925716424LJP1VMurata";

  it("recovers every field of a Mouser label whose DIs abut alphanumerics", () => {
    const b = parseBagCode(MOUSER_NO_1P);
    expect(b.customerRef).toBe("40489709");
    expect(b.lineItem).toBe("027");
    expect(b.distributorPn).toBe("C201,C202,C503");
    expect(b.quantity).toBe(100);
    expect(b.invoiceRef).toBe("092571642");
    expect(b.manufacturer).toBe("Murata");
  });

  it("flags a bag label with no 1P instead of passing the raw code on as the MPN", () => {
    const b = parseBagCode(MOUSER_NO_1P);
    expect(b.mpn).toBe("");
    expect(b.noMpn).toBe(true);
    expect(noMpnMessage(b)).toContain(
      "Murata · qty 100 · Mouser order 40489709 line 027 · customer P/N C201,C202,C503",
    );
  });

  it("keeps the traceability comment for a no-MPN label", () => {
    expect(bagComments(parseBagCode(MOUSER_NO_1P))).toBe(
      "Order 40489709 · line 027 · invoice 092571642",
    );
  });

  // Owner's real labels from one Mouser order (customer P/N entered at
  // ordering, so Mouser printed no 1P on any of them).
  it.each([
    [MOUSER_NO_1P, { lineItem: "027", distributorPn: "C201,C202,C503", quantity: 100, countryOfOrigin: "JP", manufacturer: "Murata" }],
    [
      "[)>06K4048970914K028PC102,C103Q10011K0925716424LJP1VMurata",
      { lineItem: "028", distributorPn: "C102,C103", quantity: 100, countryOfOrigin: "JP", manufacturer: "Murata" },
    ],
    [
      // The customer P/N itself starts with "Q" and abuts the line item:
      // neither "Q201" as the quantity nor "1PQ201" as an MPN.
      "[)>06K4048970914K021PQ201Q1011K0925716424LPH1Vonsemi",
      { lineItem: "021", distributorPn: "Q201", quantity: 10, countryOfOrigin: "PH", manufacturer: "onsemi" },
    ],
  ])("reads the real no-1P Mouser label %s", (raw, expected) => {
    const b = parseBagCode(raw);
    expect(b).toMatchObject({ mpn: "", noMpn: true, customerRef: "40489709", invoiceRef: "092571642", ...expected });
  });

  it("recovers the MPN from a separator-less Mouser label that has 1P", () => {
    const b = parseBagCode("[)>06K#44861 A #4492014K0171P98266-0897Q311K0781013064LHU1VMolex");
    expect(b.mpn).toBe("98266-0897");
    expect(b.noMpn).toBeUndefined();
    expect(b.customerRef).toBe("#44861 A #44920");
    expect(b.lineItem).toBe("017");
    expect(b.quantity).toBe(3);
    expect(b.invoiceRef).toBe("078101306");
    expect(b.manufacturer).toBe("Molex");
  });

  it("recovers the MPN from a separator-less DigiKey label (empty K field)", () => {
    const b = parseBagCode(
      "[)>06P296-1395-1-ND1PSN74HC595NK1K8423695110K1001234569D21451TA1B2C311K14LMYQ511ZPICK12Z1234567813Z99999920Z0000000000",
    );
    expect(b.mpn).toBe("SN74HC595N");
    expect(b.distributorPn).toBe("296-1395-1-ND");
    expect(b.poNumber).toBe("84236951");
    expect(b.dateCode).toBe("2145");
    expect(b.lotBatch).toBe("A1B2C3");
    expect(b.quantity).toBe(5);
  });

  it("does not split an MPN at letters that look like a DI", () => {
    expect(parseBagCode("[)>061PLM317TQ10")).toMatchObject({ mpn: "LM317T", quantity: 10 });
    expect(parseBagCode("[)>061PRC0603FR-0710KLQ5")).toMatchObject({ mpn: "RC0603FR-0710KL", quantity: 5 });
  });

  it("reports no MPN for a headed code it cannot read at all", () => {
    const b = parseBagCode("[)>06XYZ");
    expect(b).toMatchObject({ mpn: "", noMpn: true });
    expect(b.quantity).toBeUndefined();
  });

  it("ignores a trailing RS/EOT when it is the only separator", () => {
    expect(parseBagCode("[)>\x1e06\x1d1PFOO\x1e\x04").mpn).toBe("FOO");
  });

  it("keeps a DI-prefixed 1D code without header as the MPN guess", () => {
    expect(parseBagCode("Q100").mpn).toBe("Q100");
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

  it("test_zs_whitespace_parity", async () => {
    const base = await bagSignature("STM32F103C8T6");
    expect(await bagSignature("\u3000STM32F103C8T6\u3000")).toBe(base);
  });
});

// ---------------------------------------------------------------------------
// Fixture parity (TEST-010). The JSON fixture is the FE/BE drift contract:
// every alternate decoding inside an entry MUST hash to `expected_signature`,
// and the same digest is replayed on the BE in tests/test_bag_signature.py.
// If anybody touches normalisation on either side, exactly one of the two
// suites fails — and the fix is to update the fixture digest deliberately,
// not to paper over the difference.
// ---------------------------------------------------------------------------

describe("bagSignature fixture parity", () => {
  for (const bag of bagSignaturesFixture.bags) {
    for (const [i, raw] of bag.raws.entries()) {
      it(`hashes to expected digest — ${bag.label} [raw#${i}]`, async () => {
        const sig = await bagSignature(raw);
        expect(sig).toBe(bag.expected_signature);
      });
    }
  }

  for (const [i, [a, b]] of bagSignaturesFixture.distinct_pairs_must_differ.entries()) {
    it(`distinct pair #${i} hashes to different signatures`, async () => {
      const sa = await bagSignature(a);
      const sb = await bagSignature(b);
      expect(sa).not.toBe(sb);
      expect(sa).toBeTruthy();
      expect(sb).toBeTruthy();
    });
  }
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
