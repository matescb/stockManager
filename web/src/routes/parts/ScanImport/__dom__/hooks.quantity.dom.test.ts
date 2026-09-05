/**
 * Regression tests for `useScanImportRows().setQuantity` — units-of-measure
 * track, step 4.
 *
 * `setQuantity` used to clamp with `Math.max(0, qty | 0)`. `| 0` is
 * JavaScript's ToInt32, and the value it mangles is POSTed verbatim to
 * `bulk-import-from-scan`, so both failure modes below wrote a wrong
 * quantity into the ledger without telling anyone:
 *
 *   1. **Truncation.** A bag labelled `Q12.5` stored 12.
 *   2. **int32 wrap.** 3_000_000_000 became -1_294_967_296, which the
 *      `Math.max(0, …)` then clamped to 0 — an over-large quantity
 *      silently became *no stock at all*.
 *
 * (2) needs nothing fractional to reproduce, which is why it is pinned
 * here rather than deferred with the rest of the fractional-input work.
 * The queue's number input is still integer-only — this is about what
 * the store does with whatever it is handed.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-test" }),
}));

import { useScanImportRows } from "../hooks";
import type { Row } from "../types";

function makeRow(overrides: Partial<Row> = {}): Row {
  return {
    rowId: "row-1",
    bag: { mpn: "GRM155R71C104KA88D", raw: "raw-bag-code" },
    bagSig: null,
    quantity: 10,
    state: { kind: "pending" },
    ...overrides,
  } as Row;
}

function seeded() {
  const hook = renderHook(() => useScanImportRows());
  act(() => {
    hook.result.current.setRows([makeRow()]);
  });
  return hook;
}

beforeEach(() => {
  sessionStorage.clear();
});

describe("useScanImportRows.setQuantity", () => {
  it("keeps a whole quantity exactly as before", () => {
    const { result } = seeded();
    act(() => result.current.setQuantity("row-1", 42));
    expect(result.current.rows[0].quantity).toBe(42);
  });

  it("does not truncate a fractional quantity to an integer", () => {
    // `12.5 | 0` was 12 — a wrong number the operator would then post.
    const { result } = seeded();
    act(() => result.current.setQuantity("row-1", 12.5));
    expect(result.current.rows[0].quantity).toBe(12.5);
  });

  it("does not wrap a quantity past int32", () => {
    // `3_000_000_000 | 0` is -1_294_967_296, which the old clamp turned
    // into 0. This is a live bug with no fractional value involved.
    const { result } = seeded();
    act(() => result.current.setQuantity("row-1", 3_000_000_000));
    expect(result.current.rows[0].quantity).toBe(3_000_000_000);
  });

  it("still clamps a negative quantity to zero", () => {
    const { result } = seeded();
    act(() => result.current.setQuantity("row-1", -5));
    expect(result.current.rows[0].quantity).toBe(0);
  });

  it("treats a non-finite quantity as zero rather than storing NaN", () => {
    const { result } = seeded();
    act(() => result.current.setQuantity("row-1", NaN));
    expect(result.current.rows[0].quantity).toBe(0);
  });

  it("leaves other rows untouched", () => {
    const { result } = renderHook(() => useScanImportRows());
    act(() => {
      result.current.setRows([
        makeRow({ rowId: "a", quantity: 1 }),
        makeRow({ rowId: "b", quantity: 2 }),
      ]);
    });
    act(() => result.current.setQuantity("b", 7.25));
    expect(result.current.rows.map(r => r.quantity)).toEqual([1, 7.25]);
  });
});
