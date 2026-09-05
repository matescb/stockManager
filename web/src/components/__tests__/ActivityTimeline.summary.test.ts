/**
 * `activitySummary` — units-of-measure track, step 4.
 *
 * The activity timeline is stock *history*, so it is the one surface
 * where the unit has to come from the ledger row's own immutable stamp
 * (`stock_entries.unit`, alembic 0074) rather than the part's current
 * `unit_of_measure`. Re-resolving at read time would relabel every past
 * entry the moment a part's unit changed — exactly what an append-only
 * ledger exists to prevent.
 *
 * Prose also needs a noun where a table cell does not, so `pcs` entries
 * keep reading "12 units" (unchanged from before this step) while a
 * measured part reads "12.5 m".
 */
import { describe, it, expect } from "vitest";
import { activitySummary, type ActivityEntry } from "../ActivityTimeline";

function entry(overrides: Partial<ActivityEntry> = {}): ActivityEntry {
  return {
    kind: "stock",
    operation_type: "add",
    quantity_delta: 12,
    unit: "pcs",
    user: null,
    occurred_at: "2026-01-01T00:00:00Z",
    comments: null,
    lot_id: null,
    storage_location_id: null,
    order_id: null,
    build_id: null,
    ...overrides,
  };
}

describe("activitySummary — counted parts read exactly as before", () => {
  it("keeps the English noun for the default pcs unit", () => {
    expect(activitySummary(entry({ quantity_delta: 12 }))).toBe("Added 12 units");
    expect(activitySummary(entry({ quantity_delta: 1 }))).toBe("Added 1 unit");
  });

  it("keeps the noun when the wire carries no unit at all", () => {
    expect(activitySummary(entry({ unit: null }))).toBe("Added 12 units");
  });

  it("uses the absolute value for a negative delta", () => {
    expect(
      activitySummary(entry({ operation_type: "remove", quantity_delta: -5 })),
    ).toBe("Removed 5 units");
    expect(
      activitySummary(entry({ operation_type: "build_consume", quantity_delta: -3 })),
    ).toBe("Consumed 3 units for build");
  });

  it("keeps the signed form for an adjustment", () => {
    expect(
      activitySummary(entry({ operation_type: "adjust", quantity_delta: 4 })),
    ).toBe("Adjusted by +4");
    expect(
      activitySummary(entry({ operation_type: "adjust", quantity_delta: -4 })),
    ).toBe("Adjusted by -4");
  });
});

describe("activitySummary — measured parts carry their unit", () => {
  it("replaces the noun with the unit code", () => {
    expect(
      activitySummary(entry({ quantity_delta: 12.5, unit: "m" })),
    ).toBe("Added 12.5 m");
    expect(
      activitySummary(entry({ operation_type: "receive", quantity_delta: 250, unit: "g" })),
    ).toBe("Received 250 g");
  });

  it("does not truncate a fractional delta", () => {
    const text = activitySummary(entry({ quantity_delta: 12.5, unit: "m" }));
    expect(text).not.toBe("Added 12 m");
  });

  it("renders a whole measured quantity with no decimal tail", () => {
    expect(activitySummary(entry({ quantity_delta: 12, unit: "m" }))).toBe("Added 12 m");
  });

  it("carries the unit through an adjustment too", () => {
    expect(
      activitySummary(entry({ operation_type: "adjust", quantity_delta: 2.25, unit: "m" })),
    ).toBe("Adjusted by +2.25 m");
  });
});

describe("activitySummary — non-stock entries", () => {
  it("describes entity events without touching quantities", () => {
    expect(
      activitySummary(entry({ kind: "part_created", operation_type: null, quantity_delta: null, unit: null })),
    ).toBe("Part created");
    expect(
      activitySummary(entry({ kind: "order_updated", operation_type: null, quantity_delta: null, unit: null })),
    ).toBe("Order updated");
  });
});
