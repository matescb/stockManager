/**
 * Unit tests for the ScanImport sessionStorage draft-persistence helpers
 * (FE2-018 / #54).
 *
 * These tests exercise `storage.ts` (loadDraft / saveDraft / clearDraft)
 * in isolation without mounting the full ScanImport component — the component
 * itself brings in Scanner (camera SDK) and network calls which are not
 * relevant to persistence correctness.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { loadDraft, saveDraft, clearDraft } from "../ScanImport/storage";
import type { Row, LookupState } from "../ScanImport/types";

// ---------------------------------------------------------------------------
// Minimal Row fixtures
// ---------------------------------------------------------------------------

function makeRow(overrides: Partial<Row> = {}): Row {
  return {
    rowId: "row-1",
    bag: {
      mpn: "GRM155R71C104KA88D",
      manufacturer: "Murata",
      quantity: 10,
      raw: "raw-bag-code-abc",
    },
    bagSig: "deadbeef1234",
    quantity: 10,
    state: {
      kind: "found",
      result: {
        mpn: "GRM155R71C104KA88D",
        manufacturer: "Murata",
        description: "100nF cap",
        specs: [],
        footprint: "0402",
        category: "Capacitors",
        datasheet_url: null,
        source_url: "https://mouser.com/example",
        image_url: null,
      },
      provider: "mouser",
    },
    ...overrides,
  } as Row;
}

const WS_ID = "ws-abc-123";
const STORAGE_KEY = `scanImport:draft:${WS_ID}`;

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  sessionStorage.clear();
});

// ---------------------------------------------------------------------------
// saveDraft / loadDraft round-trip
// ---------------------------------------------------------------------------

describe("saveDraft + loadDraft", () => {
  it("persists rows and restores them with correct shape", () => {
    const row = makeRow();
    saveDraft(WS_ID, [row]);

    const restored = loadDraft(WS_ID);
    expect(restored).not.toBeNull();
    expect(restored).toHaveLength(1);
    expect(restored![0].rowId).toBe(row.rowId);
    expect(restored![0].bag.mpn).toBe(row.bag.mpn);
    expect(restored![0].bagSig).toBe(row.bagSig);
    expect(restored![0].quantity).toBe(row.quantity);
    expect(restored![0].state.kind).toBe("found");
  });

  it("coerces pending rows to error on rehydrate", () => {
    const pendingRow = makeRow({ rowId: "row-pending", state: { kind: "pending" } });
    saveDraft(WS_ID, [pendingRow]);

    const restored = loadDraft(WS_ID);
    expect(restored).not.toBeNull();
    const firstState = restored![0].state;
    expect(firstState.kind).toBe("error");
    expect((firstState as Extract<LookupState, { kind: "error" }>).message).toMatch(/interrupted/i);
  });

  it("preserves non-pending states (error, found, duplicate) verbatim", () => {
    const errorRow = makeRow({ rowId: "row-err", state: { kind: "error", message: "no match" } });
    saveDraft(WS_ID, [errorRow]);

    const restored = loadDraft(WS_ID);
    expect(restored).not.toBeNull();
    const s = restored![0].state;
    expect(s.kind).toBe("error");
    expect((s as Extract<LookupState, { kind: "error" }>).message).toBe("no match");
  });

  it("stores under workspace-specific key", () => {
    saveDraft(WS_ID, [makeRow()]);
    expect(sessionStorage.getItem(STORAGE_KEY)).not.toBeNull();
    // Different workspace should find nothing.
    expect(loadDraft("other-ws")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// loadDraft edge-cases
// ---------------------------------------------------------------------------

describe("loadDraft edge-cases", () => {
  it("returns null when no draft exists", () => {
    expect(loadDraft(WS_ID)).toBeNull();
  });

  it("returns null and removes stale entry on version mismatch", () => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ v: 999, rows: [] }));
    expect(loadDraft(WS_ID)).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("returns null and removes entry on corrupt JSON", () => {
    sessionStorage.setItem(STORAGE_KEY, "{ not valid json %%");
    expect(loadDraft(WS_ID)).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("returns null for an empty rows array", () => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ v: 1, rows: [] }));
    expect(loadDraft(WS_ID)).toBeNull();
  });

  it("returns null when wsId is empty string", () => {
    expect(loadDraft("")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// clearDraft
// ---------------------------------------------------------------------------

describe("clearDraft", () => {
  it("removes the entry from sessionStorage", () => {
    saveDraft(WS_ID, [makeRow()]);
    expect(sessionStorage.getItem(STORAGE_KEY)).not.toBeNull();
    clearDraft(WS_ID);
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("is a no-op when no draft exists", () => {
    expect(() => clearDraft(WS_ID)).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// seenSigs / seenMpns rebuild contract (logic-level, not React)
// ---------------------------------------------------------------------------

describe("loadDraft — bagSig / mpn coverage for dedup rebuild", () => {
  it("restored rows carry bagSig so caller can populate seenSigs", () => {
    const row = makeRow({ bagSig: "sha256abc" });
    saveDraft(WS_ID, [row]);
    const restored = loadDraft(WS_ID);
    expect(restored![0].bagSig).toBe("sha256abc");
  });

  it("restored rows carry bag.mpn so caller can populate seenMpns", () => {
    const row = makeRow();
    saveDraft(WS_ID, [row]);
    const restored = loadDraft(WS_ID);
    expect(restored![0].bag.mpn).toBe("GRM155R71C104KA88D");
  });

  it("null bagSig is preserved (non-crypto browsers)", () => {
    const row = makeRow({ bagSig: null });
    saveDraft(WS_ID, [row]);
    const restored = loadDraft(WS_ID);
    expect(restored![0].bagSig).toBeNull();
  });
});
