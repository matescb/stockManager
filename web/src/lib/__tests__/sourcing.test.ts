import { describe, expect, it } from "vitest";
import { bestUnitPriceAtQty, extendedPrice, lifecycleRiskTone } from "../sourcing";

describe("bestUnitPriceAtQty", () => {
  it("returns null when qty < smallest break", () => {
    expect(
      bestUnitPriceAtQty(
        [
          { quantity: 10, unit_price: 1.1 },
          { quantity: 100, unit_price: 0.9 },
        ],
        1,
      ),
    ).toBeNull();
  });

  it("picks the highest break.quantity that is <= qty", () => {
    expect(
      bestUnitPriceAtQty(
        [
          { quantity: 1, unit_price: 1.5 },
          { quantity: 10, unit_price: 1.2 },
          { quantity: 100, unit_price: 0.95 },
        ],
        50,
      ),
    ).toEqual({ unitPrice: 1.2, breakQty: 10 });
  });

  it("handles unsorted price-breaks input", () => {
    expect(
      bestUnitPriceAtQty(
        [
          { quantity: 100, unit_price: 0.8 },
          { quantity: 1, unit_price: 1.4 },
          { quantity: 10, unit_price: 1.05 },
        ],
        100,
      ),
    ).toEqual({ unitPrice: 0.8, breakQty: 100 });
  });
});

describe("extendedPrice", () => {
  it("multiplies correctly", () => {
    expect(
      extendedPrice(
        [
          { quantity: 1, unit_price: 2.5 },
          { quantity: 10, unit_price: 2.25 },
        ],
        12,
      ),
    ).toBe(27);
  });
});

describe("lifecycleRiskTone", () => {
  it("maps TrustedParts risk levels to semantic colours", () => {
    expect(lifecycleRiskTone("  LOW risk  ")).toBe("good");
    expect(lifecycleRiskTone("Medium")).toBe("warning");
    expect(lifecycleRiskTone("Med")).toBe("warning");
    expect(lifecycleRiskTone("moderate supply risk")).toBe("warning");
    expect(lifecycleRiskTone("High")).toBe("danger");
    expect(lifecycleRiskTone("Severe")).toBe("danger");
  });

  it("keeps lifecycle vocabulary precedence before generic risk levels", () => {
    expect(lifecycleRiskTone("Active high volume")).toBe("good");
    expect(lifecycleRiskTone("Obsolete - low supply")).toBe("danger");
    expect(lifecycleRiskTone("NRND low risk")).toBe("warning");
  });

  it("maps the TrustedParts Low-Med band to low-warning", () => {
    expect(lifecycleRiskTone("Low-Med")).toBe("low-warning");
    expect(lifecycleRiskTone("Low/Med")).toBe("low-warning");
  });

  it("maps descriptive Low-Med fallbacks to low-warning", () => {
    expect(lifecycleRiskTone("This product may be special order")).toBe("low-warning");
    expect(lifecycleRiskTone("Limited stock")).toBe("low-warning");
    expect(lifecycleRiskTone("Long lead times")).toBe("low-warning");
  });

  it("returns neutral for unknown vocabulary", () => {
    expect(lifecycleRiskTone("unknown status")).toBe("neutral");
  });
});
