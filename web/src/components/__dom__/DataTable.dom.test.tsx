/**
 * DOM-rendering tests for DataTable (TEST-004 / FE-008).
 *
 * Lives under `__dom__/` so vitest's `environmentMatchGlobs` runs it
 * against jsdom. The pure-helper tests in `DataTable.test.tsx` keep
 * running on the default node env.
 *
 * Pinned behaviours:
 *  - Rows render
 *  - Header click toggles sort
 *  - onRowClick fires on click
 *  - Multi-select preserves the selection set across a row-list refresh
 *    (the `pruneSelection` semantics already covered by the unit test,
 *    but this exercises the rendered table glue)
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, fireEvent, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DataTable } from "../DataTable";

type Row = { id: string; name: string; qty: number };

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

describe("DataTable (DOM)", () => {
  it("renders one row per data item", () => {
    render(
      <DataTable<Row>
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
      />,
    );
    expect(screen.getByText("Banana")).toBeDefined();
    expect(screen.getByText("Apple")).toBeDefined();
    expect(screen.getByText("Cherry")).toBeDefined();
  });

  it("clicks header to sort ascending then descending", async () => {
    const user = userEvent.setup();
    render(
      <DataTable<Row>
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
      />,
    );
    // Initial order matches input
    let cells = screen.getAllByRole("row").slice(1).map((row) =>
      within(row).getAllByRole("cell")[0].textContent,
    );
    expect(cells).toEqual(["Banana", "Apple", "Cherry"]);

    // First click: ascending — Apple, Banana, Cherry. Scope by role
    // because the column-toggle <details> also renders a "Name" label.
    const nameHeader = screen.getByRole("columnheader", { name: /name/i });
    await user.click(nameHeader);
    cells = screen.getAllByRole("row").slice(1).map((row) =>
      within(row).getAllByRole("cell")[0].textContent,
    );
    expect(cells).toEqual(["Apple", "Banana", "Cherry"]);

    // Second click: descending — Cherry, Banana, Apple
    await user.click(nameHeader);
    cells = screen.getAllByRole("row").slice(1).map((row) =>
      within(row).getAllByRole("cell")[0].textContent,
    );
    expect(cells).toEqual(["Cherry", "Banana", "Apple"]);
  });

  it("calls onRowClick when a row is clicked", async () => {
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
    await user.click(screen.getByText("Banana"));
    expect(onRowClick).toHaveBeenCalledWith(ROWS[0]);
  });

  it("multi-select preserves selection set across a row refetch (FE2-007)", () => {
    const { rerender } = render(
      <DataTable<Row>
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        selectable
      />,
    );

    // Toggle the first two rows.
    const checkboxes = screen
      .getAllByRole("checkbox")
      .filter((c) => c.getAttribute("aria-label") === "Select row");
    expect(checkboxes.length).toBe(3);
    fireEvent.click(checkboxes[0]);
    fireEvent.click(checkboxes[1]);

    // After a refetch that preserves the same ids, selection survives.
    rerender(
      <DataTable<Row>
        rows={[...ROWS]}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        selectable
      />,
    );
    const stillChecked = screen
      .getAllByRole("checkbox")
      .filter((c) => c.getAttribute("aria-label") === "Deselect row");
    expect(stillChecked.length).toBe(2);

    // After a refetch that drops one of the selected rows, that
    // selection is pruned (per pruneSelection contract).
    rerender(
      <DataTable<Row>
        rows={[ROWS[0], ROWS[2]]}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        selectable
      />,
    );
    const afterPrune = screen
      .getAllByRole("checkbox")
      .filter((c) => c.getAttribute("aria-label") === "Deselect row");
    expect(afterPrune.length).toBe(1);
  });
});
