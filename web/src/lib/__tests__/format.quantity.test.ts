/**
 * `formatQuantity` and friends — units-of-measure track, step 4.
 *
 * The three things these pin, in order of how badly they bite:
 *
 *  1. **No integer coercion, ever.** The whole point of the helper is
 *     that a 12.5 m bag never renders as 12. The rounding it does do is
 *     to the column's own `Numeric(18, 6)` scale, which cannot lose a
 *     stored digit; these cases are the guard that it never degrades
 *     into a `parseInt` / `| 0` / `Math.floor` style truncation.
 *     (The app's remaining integer coercions are the deliberate
 *     integer-only input gate and counts that are integer by contract —
 *     see `docs/frontend/quantities.md`.)
 *  2. **A whole quantity has no decimal tail.** The columns are
 *     `Numeric(18, 6)`, so a naive renderer shows `12.000000`.
 *  3. **A fractional quantity is exact.** JSON quantities are doubles,
 *     so the renderer has to absorb binary-float artifacts.
 */
import { describe, it, expect } from "vitest";
import {
  DEFAULT_QUANTITY_UNIT,
  formatQuantity,
  formatQuantityNumber,
  formatQuantityPhrase,
  quantityUnitSuffix,
} from "../format";

describe("formatQuantityNumber", () => {
  it("renders a whole quantity with no decimal tail", () => {
    expect(formatQuantityNumber(12)).toBe("12");
    expect(formatQuantityNumber(0)).toBe("0");
    expect(formatQuantityNumber(1)).toBe("1");
    expect(formatQuantityNumber(10000)).toBe("10000");
  });

  it("renders a fractional quantity exactly", () => {
    expect(formatQuantityNumber(12.5)).toBe("12.5");
    expect(formatQuantityNumber(0.25)).toBe("0.25");
    expect(formatQuantityNumber(1.000001)).toBe("1.000001");
  });

  it("absorbs binary-float artifacts by rounding to the column scale", () => {
    // The canonical example: 0.1 + 0.2 is 0.30000000000000004 as a
    // double. `Numeric(18, 6)` could only ever have stored 0.3.
    expect(formatQuantityNumber(0.1 + 0.2)).toBe("0.3");
    expect(formatQuantityNumber(1.005 * 3)).toBe("3.015");
  });

  it("never truncates toward zero the way an integer coercion would", () => {
    // Each of these is what `parseInt` / `| 0` / `~~` / `Math.floor`
    // would have produced, and each is a wrong number an operator
    // would then have acted on.
    expect(formatQuantityNumber(12.5)).not.toBe("12");
    expect(formatQuantityNumber(0.75)).not.toBe("0");
    expect(formatQuantityNumber(-1.5)).not.toBe("-1");
    expect(formatQuantityNumber(-1.5)).toBe("-1.5");
  });

  it("does not wrap past int32 the way a bitwise coercion would", () => {
    // `3000000000 | 0` is -1294967296. This is the live bug that was in
    // ScanImport/hooks.ts.
    expect(formatQuantityNumber(3_000_000_000)).toBe("3000000000");
    expect(formatQuantityNumber(2_147_483_648)).toBe("2147483648");
  });

  it("renders negative quantities (ledger removals) intact", () => {
    expect(formatQuantityNumber(-5)).toBe("-5");
    expect(formatQuantityNumber(-0.5)).toBe("-0.5");
  });

  it("normalises a value that rounds to zero from below", () => {
    // -1e-9 is below the column scale, so the server would store 0.
    // Rendering it as "-0" would be noise.
    expect(formatQuantityNumber(-1e-9)).toBe("0");
    expect(formatQuantityNumber(1e-9)).toBe("0");
  });

  it("returns empty for a non-finite value", () => {
    expect(formatQuantityNumber(NaN)).toBe("");
    expect(formatQuantityNumber(Infinity)).toBe("");
  });

  it("adds no thousands separators", () => {
    // Quantities get read back against printed bag labels and typed
    // into integer-only inputs; "10,000" matches neither.
    expect(formatQuantityNumber(1234567)).toBe("1234567");
  });
});

describe("quantityUnitSuffix", () => {
  it("suppresses the default pcs unit on screen", () => {
    expect(quantityUnitSuffix("pcs")).toBe("");
    expect(quantityUnitSuffix("PCS")).toBe("");
    expect(quantityUnitSuffix(" pcs ")).toBe("");
    expect(DEFAULT_QUANTITY_UNIT).toBe("pcs");
  });

  it("shows a measured unit", () => {
    expect(quantityUnitSuffix("m")).toBe("m");
    expect(quantityUnitSuffix("g")).toBe("g");
  });

  it("shows pcs when the caller opts in (print)", () => {
    expect(quantityUnitSuffix("pcs", true)).toBe("pcs");
  });

  it("returns empty for a missing unit", () => {
    expect(quantityUnitSuffix(null)).toBe("");
    expect(quantityUnitSuffix(undefined)).toBe("");
    expect(quantityUnitSuffix("")).toBe("");
  });
});

describe("formatQuantity", () => {
  it("renders a bare number when the unit is absent or pcs", () => {
    expect(formatQuantity(12)).toBe("12");
    expect(formatQuantity(12, "pcs")).toBe("12");
    expect(formatQuantity(12, null)).toBe("12");
  });

  it("renders the unit alongside a measured quantity", () => {
    expect(formatQuantity(12.5, "m")).toBe("12.5 m");
    expect(formatQuantity(250, "g")).toBe("250 g");
  });

  it("shows pcs when the caller opts in", () => {
    expect(formatQuantity(12, "pcs", { alwaysShowUnit: true })).toBe("12 pcs");
    // Opting in does not invent a unit that isn't there.
    expect(formatQuantity(12, null, { alwaysShowUnit: true })).toBe("12");
  });

  it("returns the fallback for a nullish quantity", () => {
    expect(formatQuantity(null)).toBe("");
    expect(formatQuantity(undefined)).toBe("");
    expect(formatQuantity(null, "m", { fallback: "—" })).toBe("—");
  });

  it("renders zero rather than treating it as absent", () => {
    expect(formatQuantity(0)).toBe("0");
    expect(formatQuantity(0, "m")).toBe("0 m");
    expect(formatQuantity(0, null, { fallback: "—" })).toBe("0");
  });

  it("accepts a numeric string without a prefix-parse", () => {
    expect(formatQuantity("12.5", "m")).toBe("12.5 m");
    // `parseFloat("12.5abc")` is 12.5 — a silent prefix parse. `Number`
    // rejects it, which is what we want for an unexpected wire value.
    expect(formatQuantity("12.5abc", "m")).toBe("");
    expect(formatQuantity("")).toBe("");
  });
});

describe("formatQuantityPhrase", () => {
  it("uses the English noun when the unit is the default", () => {
    expect(formatQuantityPhrase(12)).toBe("12 units");
    expect(formatQuantityPhrase(1)).toBe("1 unit");
    expect(formatQuantityPhrase(12, "pcs")).toBe("12 units");
    expect(formatQuantityPhrase(0)).toBe("0 units");
  });

  it("replaces the noun with a measured unit", () => {
    expect(formatQuantityPhrase(12.5, "m")).toBe("12.5 m");
    expect(formatQuantityPhrase(1, "m")).toBe("1 m");
  });

  it("keeps fractional counts exact", () => {
    expect(formatQuantityPhrase(0.5)).toBe("0.5 units");
  });

  it("returns empty for a nullish quantity", () => {
    expect(formatQuantityPhrase(null)).toBe("");
  });
});
