/**
 * DOM tests for ScanImportQueue (#119 — ScanImport split).
 *
 * Exercises rendering of different row states without mounting the camera
 * SDK or making network requests.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import ScanImportQueue from "../ScanImportQueue";
import type { Row } from "../types";

// ─── fixtures ─────────────────────────────────────────────────────────────────

function makeBag(mpn = "GRM155R71C104KA88D") {
  return { mpn, manufacturer: "Murata", quantity: 10, raw: "raw" };
}

function makeRow(overrides: Partial<Row>): Row {
  return {
    rowId: "r1",
    bag: makeBag(),
    bagSig: "sig1",
    quantity: 10,
    state: { kind: "pending" },
    ...overrides,
  };
}

const FOUND_STATE: Row["state"] = {
  kind: "found",
  result: {
    mpn: "GRM155R71C104KA88D",
    manufacturer: "Murata",
    description: "100nF 0402",
    category: "Capacitors",
    footprint: "0402",
    datasheet_url: null,
    image_url: null,
    source_url: "https://mouser.com/x",
    specs: [{ key: "Capacitance", value: "100nF" }],
  },
  provider: "mouser",
};

const DUPLICATE_STATE: Row["state"] = {
  kind: "duplicate",
  existing: {
    id: "part-abc",
    part_type: "local",
    name: "GRM Capacitor",
    mpn: "GRM155R71C104KA88D",
    manufacturer: "Murata",
    internal_part_number: null,
    description: null,
    footprint: null,
    notes_markdown: null,
    low_stock_report_quantity: null,
    attrition_percentage: 0,
    attrition_min_quantity: 0,
    default_storage_location_id: null,
    default_storage_mandatory: false,
    serialized: false,
    linked_provider: null,
    linked_external_id: null,
    last_refresh_at: null,
    description_locally_edited: false,
    archived_at: null,
    on_hand: null,
    reserved: 0,
    available: 0,
    image_url: null,
  },
};

const BAG_RESCAN_STATE: Row["state"] = {
  kind: "bag_rescan",
  part_id: "part-xyz",
  lot_id: null,
  storage_location_id: null,
  quantity: 5,
};

// ─── helpers ──────────────────────────────────────────────────────────────────

function renderQueue(rows: Row[], overrides?: Partial<Parameters<typeof ScanImportQueue>[0]>) {
  const onRemove = vi.fn();
  const onQuantity = vi.fn();
  const onOpenExisting = vi.fn();
  const onQuickRemove = vi.fn();
  render(
    <ScanImportQueue
      rows={rows}
      onRemove={onRemove}
      onQuantity={onQuantity}
      onOpenExisting={onOpenExisting}
      onQuickRemove={onQuickRemove}
      {...overrides}
    />,
  );
  return { onRemove, onQuantity, onOpenExisting, onQuickRemove };
}

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ─── tests ────────────────────────────────────────────────────────────────────

describe("ScanImportQueue", () => {
  it("shows empty-state message when rows is empty", () => {
    renderQueue([]);
    expect(screen.getByText(/No scans yet/i)).toBeTruthy();
  });

  it("renders the MPN for each row", () => {
    const rows = [
      makeRow({ rowId: "r1", bag: makeBag("MPN-001") }),
      makeRow({ rowId: "r2", bag: makeBag("MPN-002") }),
    ];
    renderQueue(rows);
    expect(screen.getByText("MPN-001")).toBeTruthy();
    expect(screen.getByText("MPN-002")).toBeTruthy();
  });

  it("renders a spinner for pending rows", () => {
    renderQueue([makeRow({ state: { kind: "pending" } })]);
    expect(screen.getByText(/Looking up/i)).toBeTruthy();
  });

  it("renders duplicate warning and part name", () => {
    renderQueue([makeRow({ state: DUPLICATE_STATE })]);
    expect(screen.getByText(/Already in library/i)).toBeTruthy();
    expect(screen.getByText("GRM Capacitor")).toBeTruthy();
  });

  it("calls onRemove when the trash button is clicked", () => {
    const { onRemove } = renderQueue([makeRow({ rowId: "row-99" })]);
    const trashBtn = screen.getByRole("button", { name: /Remove GRM155R71C104KA88D/i });
    fireEvent.click(trashBtn);
    expect(onRemove).toHaveBeenCalledWith("row-99");
  });

  it("renders found details with manufacturer", () => {
    renderQueue([makeRow({ state: FOUND_STATE })]);
    expect(screen.getByText("Murata")).toBeTruthy();
    expect(screen.getByText("Mouser")).toBeTruthy();
  });

  it("renders bag_rescan state with RotateCcw icon text", () => {
    renderQueue([makeRow({ state: BAG_RESCAN_STATE })]);
    expect(screen.getByText(/Recognised/i)).toBeTruthy();
    expect(screen.getByText(/bag had qty/i)).toBeTruthy();
  });

  it("calls onQuickRemove when Remove button is clicked in bag_rescan card", () => {
    const { onQuickRemove } = renderQueue([makeRow({ rowId: "row-rescan", state: BAG_RESCAN_STATE })]);
    const removeBtn = screen.getByRole("button", { name: /Remove 1/i });
    fireEvent.click(removeBtn);
    expect(onQuickRemove).toHaveBeenCalledWith("row-rescan", 1);
  });

  it("renders error state with message", () => {
    renderQueue([makeRow({ state: { kind: "error", message: "Service unavailable" } })]);
    expect(screen.getByText("Service unavailable")).toBeTruthy();
  });

  it("renders consumed state", () => {
    renderQueue([
      makeRow({ state: { kind: "consumed", partId: "part-abc", quantity: 3 } }),
    ]);
    expect(screen.getByText(/Removed 3 from this bag/i)).toBeTruthy();
  });

  it("calls onOpenExisting for duplicate row", () => {
    const { onOpenExisting } = renderQueue([makeRow({ rowId: "dup-row", state: DUPLICATE_STATE })]);
    const openBtn = screen.getByRole("button", { name: /Open existing part/i });
    fireEvent.click(openBtn);
    expect(onOpenExisting).toHaveBeenCalledWith(
      expect.objectContaining({ rowId: "dup-row" }),
    );
  });

  it("calls onQuantity when qty input changes in found row", () => {
    const { onQuantity } = renderQueue([makeRow({ rowId: "found-row", state: FOUND_STATE, quantity: 5 })]);
    // The qty label is not associated via htmlFor — query by value/type directly.
    const inputs = screen.getAllByRole("spinbutton");
    // The qty input for a found row is the one with value "5".
    const qtyInput = inputs.find(el => (el as HTMLInputElement).value === "5")!;
    fireEvent.change(qtyInput, { target: { value: "12" } });
    expect(onQuantity).toHaveBeenCalledWith("found-row", 12);
  });
});
