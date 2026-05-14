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
  localStorage.clear();
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

  it("activates onRowClick via Enter key", () => {
    const onRowClick = vi.fn();
    render(
      <DataTable<Row>
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        onRowClick={onRowClick}
      />,
    );
    // When onRowClick is set, data <tr> elements get tabIndex=0 and an aria-label.
    const dataRows = screen.getAllByRole("row").slice(1);
    fireEvent.keyDown(dataRows[0], { key: "Enter" });
    expect(onRowClick).toHaveBeenCalledWith(ROWS[0]);
  });

  it("activates onRowClick via Space key", () => {
    const onRowClick = vi.fn();
    render(
      <DataTable<Row>
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        onRowClick={onRowClick}
      />,
    );
    const dataRows = screen.getAllByRole("row").slice(1);
    fireEvent.keyDown(dataRows[0], { key: " " });
    expect(onRowClick).toHaveBeenCalledWith(ROWS[0]);
  });

  it("does not activate onRowClick if checkbox Space is pressed", () => {
    const onRowClick = vi.fn();
    render(
      <DataTable<Row>
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        onRowClick={onRowClick}
        selectable
      />,
    );
    // The checkbox <td> stops keydown propagation for Enter/Space, so
    // firing Space on the td (not the row itself) should NOT reach the row handler.
    const dataRows = screen.getAllByRole("row").slice(1);
    const checkboxTd = dataRows[0].querySelector("td");
    fireEvent.keyDown(checkboxTd!, { key: " " });
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it("respects initialSearch prop on first render", () => {
    render(
      <DataTable<Row>
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        initialSearch="ban"
      />,
    );
    const input = screen.getByRole<HTMLInputElement>("textbox");
    expect(input.value).toBe("ban");
    expect(screen.getByText("Banana")).toBeDefined();
    expect(screen.queryByText("Apple")).toBeNull();
    expect(screen.queryByText("Cherry")).toBeNull();
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

  it("resets persisted column prefs when the workspace changes", () => {
    localStorage.setItem("workspaceId", "ws-a");
    const { rerender } = render(
      <DataTable<Row>
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        tableId="prefs-test"
      />,
    );

    fireEvent.click(screen.getByRole("checkbox", { name: "Qty" }));
    expect(screen.queryByRole("columnheader", { name: /qty/i })).toBeNull();
    expect(localStorage.getItem("ws:ws-a:dt:prefs-test")).toContain('"qty":true');

    localStorage.setItem("workspaceId", "ws-b");
    rerender(
      <DataTable<Row>
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        tableId="prefs-test"
      />,
    );
    expect(screen.getByRole("columnheader", { name: /qty/i })).toBeDefined();

    localStorage.setItem("workspaceId", "ws-a");
    rerender(
      <DataTable<Row>
        rows={ROWS}
        columns={COLUMNS}
        rowKey={(r) => r.id}
        tableId="prefs-test"
      />,
    );
    expect(screen.queryByRole("columnheader", { name: /qty/i })).toBeNull();
  });
});
