import { describe, expect, it } from "vitest";
import { firstMissing, followUpPrompt, parseFollowUpAnswer } from "./followUp";

describe("follow-up helpers", () => {
  it("asks for the MPN first, then the quantity", () => {
    expect(firstMissing({ mpn: "", quantity: 0 })).toBe("mpn");
    expect(firstMissing({ mpn: "X", quantity: 0 })).toBe("quantity");
    expect(firstMissing({ mpn: "X", quantity: 3 })).toBeNull();
  });

  it("names the bag in the prompt", () => {
    expect(followUpPrompt({ missing: "mpn", context: "Murata · qty 100" })).toBe(
      "Scan the Mfr. Part No barcode on this label (Murata · qty 100)",
    );
    expect(followUpPrompt({ missing: "quantity", context: "" })).toBe("Scan or type the quantity");
  });

  it("parses answers and refuses a 2D label or a non-number quantity", () => {
    expect(parseFollowUpAnswer("mpn", " GRM188 ")).toEqual({ mpn: "GRM188" });
    expect(parseFollowUpAnswer("mpn", "[)>06K1")).toBeNull();
    expect(parseFollowUpAnswer("quantity", "Q250")).toEqual({ quantity: 250 });
    expect(parseFollowUpAnswer("quantity", "25")).toEqual({ quantity: 25 });
    expect(parseFollowUpAnswer("quantity", "0")).toBeNull();
    expect(parseFollowUpAnswer("quantity", "abc")).toBeNull();
  });
});
