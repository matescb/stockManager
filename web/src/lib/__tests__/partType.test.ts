import { describe, expect, it } from "vitest";
import { partTypeLabel } from "../partType";

const part = (part_type: string, linked_provider: string | null = null) =>
  ({ part_type, linked_provider }) as Parameters<typeof partTypeLabel>[0];

describe("partTypeLabel", () => {
  it("names the provider that backs a linked part", () => {
    expect(partTypeLabel(part("linked", "digikey"))).toBe("linked · DigiKey");
    expect(partTypeLabel(part("linked", "mouser"))).toBe("linked · Mouser");
  });

  it("falls back to the raw provider name for one the UI doesn't know", () => {
    expect(partTypeLabel(part("linked", "octopart"))).toBe("linked · octopart");
  });

  it("shows the bare type when no provider backs the part", () => {
    expect(partTypeLabel(part("local"))).toBe("local");
    expect(partTypeLabel(part("meta"))).toBe("meta");
    expect(partTypeLabel(part("linked"))).toBe("linked");
  });

  it("writes sub_assembly the way the UI spells it", () => {
    expect(partTypeLabel(part("sub_assembly"))).toBe("sub-assembly");
  });

  it("keeps a declared role visible when a provider also backs it", () => {
    // A meta-part can carry an MPN and be refreshed. That describes
    // where its metadata came from, not what the part is — so the role
    // stays in front and is never replaced by "linked".
    expect(partTypeLabel(part("meta", "mouser"))).toBe("meta · Mouser");
    expect(partTypeLabel(part("sub_assembly", "mouser"))).toBe("sub-assembly · Mouser");
  });

  it("treats an empty provider string as no provider", () => {
    expect(partTypeLabel(part("linked", ""))).toBe("linked");
  });

  it("passes an unrecognised type through untouched", () => {
    expect(partTypeLabel(part("component"))).toBe("component");
  });
});
