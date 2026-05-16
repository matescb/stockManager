/**
 * Unit tests for the narrow-invalidation helpers introduced in FE2-017.
 *
 * Each helper returns `unknown[][]` — a list of query keys. We verify:
 *   1. Return type is an array of arrays.
 *   2. Every key starts with ["ws", workspaceId] (workspace-scoped invariant).
 *   3. The expected resource segments appear in at least one key.
 */
import { describe, it, expect } from "vitest";
import {
  archivePartKeys,
  archiveStorageKeys,
  lotMutationKeys,
  archiveProjectKeys,
  stockReportKeys,
} from "./queryKeys";

const WS = "ws-abc";
const PART_ID = "part-001";
const STORAGE_ID = "storage-001";
const LOT_ID = "lot-001";
const PROJECT_ID = "proj-001";

function allStartWithWsPrefix(keys: unknown[][], ws: string): boolean {
  return keys.every(k => Array.isArray(k) && k[0] === "ws" && k[1] === ws);
}

describe("archivePartKeys", () => {
  const keys = archivePartKeys(WS, PART_ID);

  it("returns an array of arrays", () => {
    expect(Array.isArray(keys)).toBe(true);
    keys.forEach(k => expect(Array.isArray(k)).toBe(true));
  });

  it("every key is workspace-scoped", () => {
    expect(allStartWithWsPrefix(keys, WS)).toBe(true);
  });

  it("includes a parts list key", () => {
    expect(keys.some(k => (k as unknown[]).includes("parts"))).toBe(true);
  });

  it("includes a part detail key containing the partId", () => {
    expect(keys.some(k => (k as unknown[]).includes(PART_ID))).toBe(true);
  });

  it("includes low-stock and stock-value report keys", () => {
    const flat = keys.map(k => (k as unknown[]).join(","));
    expect(flat.some(s => s.includes("low-stock"))).toBe(true);
    expect(flat.some(s => s.includes("stock-value"))).toBe(true);
  });

  it("handles null workspaceId by substituting 'none'", () => {
    const k = archivePartKeys(null, PART_ID);
    expect(k.every(row => (row as unknown[])[1] === "none")).toBe(true);
  });
});

describe("archiveStorageKeys", () => {
  const keys = archiveStorageKeys(WS, STORAGE_ID);

  it("returns an array of arrays", () => {
    expect(Array.isArray(keys)).toBe(true);
    keys.forEach(k => expect(Array.isArray(k)).toBe(true));
  });

  it("every key is workspace-scoped", () => {
    expect(allStartWithWsPrefix(keys, WS)).toBe(true);
  });

  it("includes a storage list key", () => {
    expect(keys.some(k => (k as unknown[]).includes("storage"))).toBe(true);
  });

  it("includes the specific storage id", () => {
    expect(keys.some(k => (k as unknown[]).includes(STORAGE_ID))).toBe(true);
  });

  it("includes a stock-value report key", () => {
    const flat = keys.map(k => (k as unknown[]).join(","));
    expect(flat.some(s => s.includes("stock-value"))).toBe(true);
  });
});

describe("lotMutationKeys", () => {
  const lot = { id: LOT_ID, part_id: PART_ID };

  it("returns an array of arrays without extra storageIds", () => {
    const keys = lotMutationKeys(WS, lot);
    expect(Array.isArray(keys)).toBe(true);
    keys.forEach(k => expect(Array.isArray(k)).toBe(true));
  });

  it("every key is workspace-scoped", () => {
    const keys = lotMutationKeys(WS, lot);
    expect(allStartWithWsPrefix(keys, WS)).toBe(true);
  });

  it("includes lots list key", () => {
    const keys = lotMutationKeys(WS, lot);
    expect(keys.some(k => (k as unknown[]).includes("lots"))).toBe(true);
  });

  it("includes lot detail key", () => {
    const keys = lotMutationKeys(WS, lot);
    expect(keys.some(k => (k as unknown[]).includes(LOT_ID))).toBe(true);
  });

  it("includes parts list and part detail keys", () => {
    const keys = lotMutationKeys(WS, lot);
    expect(keys.some(k => (k as unknown[]).includes("parts"))).toBe(true);
    expect(keys.some(k => (k as unknown[]).includes(PART_ID))).toBe(true);
  });

  it("includes the three stock-rollup report keys", () => {
    const keys = lotMutationKeys(WS, lot);
    const flat = keys.map(k => (k as unknown[]).join(","));
    expect(flat.some(s => s.includes("low-stock"))).toBe(true);
    expect(flat.some(s => s.includes("stock-value"))).toBe(true);
    expect(flat.some(s => s.includes("expiring"))).toBe(true);
  });

  it("appends a key per extra storageId", () => {
    const withStorage = lotMutationKeys(WS, lot, [STORAGE_ID]);
    const without = lotMutationKeys(WS, lot, []);
    expect(withStorage.length).toBe(without.length + 1);
    expect(withStorage.some(k => (k as unknown[]).includes(STORAGE_ID))).toBe(true);
  });

  it("supports multiple storageIds", () => {
    const S2 = "storage-002";
    const keys = lotMutationKeys(WS, lot, [STORAGE_ID, S2]);
    expect(keys.some(k => (k as unknown[]).includes(STORAGE_ID))).toBe(true);
    expect(keys.some(k => (k as unknown[]).includes(S2))).toBe(true);
  });
});

describe("stockReportKeys", () => {
  it("returns the stock-rollup report keys for a workspace", () => {
    expect(stockReportKeys(WS)).toEqual([
      ["ws", WS, "report", "low-stock"],
      ["ws", WS, "report", "stock-value"],
      ["ws", WS, "report", "expiring"],
    ]);
  });
});

describe("archiveProjectKeys", () => {
  const keys = archiveProjectKeys(WS, PROJECT_ID);

  it("returns an array of arrays", () => {
    expect(Array.isArray(keys)).toBe(true);
    keys.forEach(k => expect(Array.isArray(k)).toBe(true));
  });

  it("every key is workspace-scoped", () => {
    expect(allStartWithWsPrefix(keys, WS)).toBe(true);
  });

  it("includes a projects list key", () => {
    expect(keys.some(k => (k as unknown[]).includes("projects"))).toBe(true);
  });

  it("includes the project detail key with project id", () => {
    expect(keys.some(k => (k as unknown[]).includes(PROJECT_ID))).toBe(true);
  });
});
