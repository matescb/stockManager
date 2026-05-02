/**
 * DOM tests for keyboard activation on clickable DataTable rows (FE-008 / issue #42).
 *
 * Pinned behaviours:
 *  - Tab-focusable rows exist when onRowClick is provided
 *  - Pressing Enter on a focused row fires onRowClick with the correct row object
 *  - Pressing Space on a focused row fires onRowClick with the correct row object
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DataTable } from "../DataTable";

type Row = { id: string; name: string; qty: number };

// Re-use the same fixture shapes as DataTable.dom.test.tsx
const ROWS: Row[] = [
  { id: "a", name: "Banana", qty: 3 },
  { id: "b", name: "Apple", qty: 10 },
  { id: "c", name: "Cherry", qty: 1 },
];

const COLUMNS = [
  { key: "name", header: "Name", accessor: (r: Row) => r.name },
  { key: "qty", header: "Qty", accessor: (r: Row) => r.qty },
];

beforeEach(() => {
  cleanup();
});

describe("DataTable keyboard activation", () => {
  it("rows have tabIndex=0 when onRowClick is provided", () => {
    render(
      <DataTable<Row>
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        onRowClick={vi.fn()}
      />,
    );
    // Data rows (not header) should be focusable
    const dataRows = screen.getAllByRole("row").slice(1);
    expect(dataRows.length).toBe(3);
    for (const row of dataRows) {
      expect(row.getAttribute("tabindex")).toBe("0");
    }
  });

  it("pressing Enter on a focused row fires onRowClick with the row data", async () => {
    const onRowClick = vi.fn();
    const user = userEvent.setup();
    render(
      <DataTable<Row>
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        onRowClick={onRowClick}
      />,
    );
    const dataRows = screen.getAllByRole("row").slice(1);
    // Focus the first data row (Banana) and press Enter
    dataRows[0].focus();
    await user.keyboard("{Enter}");
    expect(onRowClick).toHaveBeenCalledTimes(1);
    expect(onRowClick).toHaveBeenCalledWith(ROWS[0]);
  });

  it("pressing Space on a focused row fires onRowClick with the row data", async () => {
    const onRowClick = vi.fn();
    const user = userEvent.setup();
    render(
      <DataTable<Row>
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        onRowClick={onRowClick}
      />,
    );
    const dataRows = screen.getAllByRole("row").slice(1);
    // Focus the second data row (Apple) and press Space
    dataRows[1].focus();
    await user.keyboard(" ");
    expect(onRowClick).toHaveBeenCalledTimes(1);
    expect(onRowClick).toHaveBeenCalledWith(ROWS[1]);
  });

  it("rows without onRowClick are not keyboard-focusable", () => {
    render(
      <DataTable<Row>
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
      />,
    );
    const dataRows = screen.getAllByRole("row").slice(1);
    for (const row of dataRows) {
      expect(row.getAttribute("tabindex")).toBeNull();
    }
  });
});
