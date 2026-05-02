/**
 * DOM tests for ScanImportActions (#119 — ScanImport split).
 *
 * Verifies the submit button, storage selector, and last-summary card
 * render and respond correctly, without any network or camera interaction.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import ScanImportActions from "../ScanImportActions";
import type { ImportResponse } from "../types";
import type { StorageLocation } from "@/types";

// ─── fixtures ─────────────────────────────────────────────────────────────────

const STORAGES: StorageLocation[] = [
  {
    id: "s1",
    name: "Shelf A",
    archived_at: null,
    description: null,
    single_part_only: false,
    existing_parts_only: false,
    is_full: false,
  },
  {
    id: "s2",
    name: "Shelf B (archived)",
    archived_at: "2024-02-01T00:00:00Z",
    description: null,
    single_part_only: false,
    existing_parts_only: false,
    is_full: false,
  },
];

const SUMMARY_ALL_CREATED: ImportResponse = {
  rows: [],
  summary: { created: 3, duplicate: 0, bag_rescan: 0, lookup_failed: 0, invalid: 0 },
  provider: "mouser",
};

const SUMMARY_WITH_DUPES: ImportResponse = {
  rows: [],
  summary: { created: 1, duplicate: 2, bag_rescan: 0, lookup_failed: 1, invalid: 0 },
  provider: "mouser",
};

// ─── helpers ──────────────────────────────────────────────────────────────────

function renderActions(overrides: Partial<Parameters<typeof ScanImportActions>[0]> = {}) {
  const onStorageChange = vi.fn();
  const onSubmit = vi.fn();
  render(
    <ScanImportActions
      rowCount={2}
      importableCount={2}
      submitting={false}
      storageId=""
      storages={STORAGES}
      lastSummary={null}
      onStorageChange={onStorageChange}
      onSubmit={onSubmit}
      {...overrides}
    />,
  );
  return { onStorageChange, onSubmit };
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

describe("ScanImportActions", () => {
  it("renders the scanned row count in the heading", () => {
    renderActions({ rowCount: 5 });
    expect(screen.getByText("Scanned (5)")).toBeTruthy();
  });

  it("shows importable count on the import button", () => {
    renderActions({ importableCount: 3 });
    expect(screen.getByRole("button", { name: /Import \(3\)/i })).toBeTruthy();
  });

  it("disables import button when importableCount is 0", () => {
    renderActions({ importableCount: 0 });
    const btn = screen.getByRole("button", { name: /Import/i });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  it("disables import button while submitting", () => {
    renderActions({ importableCount: 2, submitting: true });
    const btn = screen.getByRole("button", { name: /Import/i });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  it("calls onSubmit when import button is clicked", () => {
    const { onSubmit } = renderActions({ importableCount: 1 });
    fireEvent.click(screen.getByRole("button", { name: /Import/i }));
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it("renders active (non-archived) storage options", () => {
    renderActions();
    expect(screen.getByRole("option", { name: "Shelf A" })).toBeTruthy();
  });

  it("hides archived storage locations", () => {
    renderActions();
    expect(screen.queryByRole("option", { name: /Shelf B/i })).toBeNull();
  });

  it("calls onStorageChange when storage select changes", () => {
    const { onStorageChange } = renderActions();
    const select = screen.getByRole("combobox");
    fireEvent.change(select, { target: { value: "s1" } });
    expect(onStorageChange).toHaveBeenCalledWith("s1");
  });

  it("does not render summary card when lastSummary is null", () => {
    renderActions({ lastSummary: null });
    expect(screen.queryByText(/Last import/i)).toBeNull();
  });

  it("renders summary card with created count", () => {
    renderActions({ lastSummary: SUMMARY_ALL_CREATED });
    expect(screen.getByText(/Last import/i)).toBeTruthy();
    expect(screen.getByText("3 created")).toBeTruthy();
  });

  it("renders duplicate and not-found counts in summary", () => {
    renderActions({ lastSummary: SUMMARY_WITH_DUPES });
    expect(screen.getByText("2 duplicate")).toBeTruthy();
    expect(screen.getByText("1 not found")).toBeTruthy();
  });

  it("does not render duplicate count when zero", () => {
    renderActions({ lastSummary: SUMMARY_ALL_CREATED });
    expect(screen.queryByText(/duplicate/i)).toBeNull();
  });
});
