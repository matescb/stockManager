import { describe, expect, it } from "vitest";
import { bestUnitPriceAtQty, extendedPrice } from "../sourcing";

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
