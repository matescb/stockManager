import { describe, expect, it } from "vitest";
import type { BoxBag } from "./api";
import { auditLines, findBySignature, splitBags, summarize } from "./auditState";

const part = { id: "p1", name: "LM358", mpn: "LM358", manufacturer: null, description: null };
const bags: BoxBag[] = [
  { part, lot: { id: "l1", name: "Bag 1" }, quantity: 10, bag_signatures: ["s1"] },
  { part, lot: { id: "l2", name: "Bag 2" }, quantity: 20, bag_signatures: ["s2"] },
  { part, lot: null, quantity: 77, bag_signatures: [] },
];

describe("audit state", () => {
  it("finds a recorded bag by its signature", () => {
    expect(findBySignature(bags, "s2")?.lot?.id).toBe("l2");
    expect(findBySignature(bags, "nope")).toBeUndefined();
    expect(findBySignature(bags, null)).toBeUndefined();
  });

  it("splits scanned from not scanned", () => {
    const { scanned, unscanned } = splitBags(bags, { counted: { "lot:l1": 10 }, missing: {} });
    expect(scanned.map(b => b.lot?.id)).toEqual(["l1"]);
    expect(unscanned).toHaveLength(2);
  });

  it("writes off unscanned bags by default and sends only changed counts", () => {
    const lines = auditLines(bags, { counted: { "lot:l1": 10, "lot:l2": 18 }, missing: {} });
    expect(lines).toEqual([
      { part_id: "p1", lot_id: "l2", action: "count", actual_quantity: 18 },
      { part_id: "p1", lot_id: null, action: "write_off" },
    ]);
  });

  it("honours keep and move verdicts", () => {
    const s = summarize(bags, {
      counted: {},
      missing: { "lot:l1": { action: "keep" }, "lot:l2": { action: "move", to: "box2" }, "part:p1": { action: "move" } },
    });
    expect(s.lines).toEqual([
      { part_id: "p1", lot_id: "l2", action: "move", to_storage_location_id: "box2" },
    ]);
    expect(s.move).toBe(1);
    expect(s.writeOff).toBe(0);
  });
});
