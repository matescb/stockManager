/**
 * `quantityColumn` — units-of-measure track, step 4.
 *
 * The naive way to put a unit in a quantity column is to make the
 * `accessor` return `"12.5 m"`. That silently breaks three separate
 * things in `DataTable`, and each of them is pinned here:
 *
 *   1. **Sort.** `sorted` compares accessor values with `<` / `>`, so a
 *      string accessor sorts `"10 m"` before `"9 m"`.
 *   2. **CSV export.** `cellText` prefers the accessor, so a formatted
 *      accessor exports text into a column a spreadsheet wants to sum —
 *      and a `render`-only column with *no* accessor exports an empty
 *      cell, which is the opposite trap.
 *   3. **Alignment.** `defaultAlignFor` right-aligns only when the
 *      accessor returns a `number`.
 *
 * The split — numeric `accessor`, formatted `render` — keeps all three.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DataTable, quantityColumn } from "../DataTable";

type Row = { id: string; name: string; qty: number | null; unit: string };

const ROWS: Row[] = [
  { id: "a", name: "Wire", qty: 10, unit: "m" },
  { id: "b", name: "Paste", qty: 9.5, unit: "m" },
  { id: "c", name: "Resistor", qty: 100, unit: "pcs" },
];

const COLUMNS = [
  { key: "name", header: "Name", accessor: (r: Row) => r.name },
  quantityColumn<Row>({
    key: "qty",
    header: "Qty",
    value: r => r.qty,
    unit: r => r.unit,
  }),
];

let capturedBlob: Blob | null = null;

beforeEach(() => {
  cleanup();
  localStorage.clear();
  capturedBlob = null;
  URL.createObjectURL = vi.fn((blob: Blob) => {
    capturedBlob = blob;
    return "blob:mock";
  });
  URL.revokeObjectURL = vi.fn();
});

function qtyCells(): (string | null)[] {
  return screen
    .getAllByRole("row")
    .slice(1)
    .map(row => within(row).getAllByRole("cell")[1].textContent);
}

describe("quantityColumn — rendering", () => {
  it("renders the quantity with its unit, suppressing the default pcs", () => {
    render(<DataTable<Row> rows={ROWS} columns={COLUMNS} rowKey={r => r.id} />);
    expect(qtyCells()).toEqual(["10 m", "9.5 m", "100"]);
  });

  it("renders a fractional quantity exactly and a whole one with no tail", () => {
    render(
      <DataTable<Row>
        rows={[
          { id: "a", name: "A", qty: 12, unit: "m" },
          { id: "b", name: "B", qty: 12.5, unit: "m" },
          // 0.1 + 0.2 is 0.30000000000000004 as a double.
          { id: "c", name: "C", qty: 0.1 + 0.2, unit: "m" },
        ]}
        columns={COLUMNS}
        rowKey={r => r.id}
      />,
    );
    expect(qtyCells()).toEqual(["12 m", "12.5 m", "0.3 m"]);
  });

  it("lets a caller wrap the formatted text without losing the accessor", () => {
    render(
      <DataTable<Row>
        rows={ROWS}
        columns={[
          { key: "name", header: "Name", accessor: (r: Row) => r.name },
          quantityColumn<Row>({
            key: "qty",
            header: "Qty",
            value: r => r.qty,
            unit: r => r.unit,
            render: (text, r) => (
              <span className={r.qty && r.qty < 10 ? "text-danger" : ""}>{text}</span>
            ),
          }),
        ]}
        rowKey={r => r.id}
      />,
    );
    expect(qtyCells()).toEqual(["10 m", "9.5 m", "100"]);
    expect(screen.getByText("9.5 m").className).toBe("text-danger");
  });
});

describe("quantityColumn — sorting stays numeric", () => {
  it("sorts fractional quantities by value, not by rendered string", async () => {
    const user = userEvent.setup();
    render(<DataTable<Row> rows={ROWS} columns={COLUMNS} rowKey={r => r.id} />);

    await user.click(screen.getByRole("columnheader", { name: /qty/i }));
    // Numeric ascending: 9.5, 10, 100. A string sort would give
    // "10 m", "100", "9.5 m" — the classic lexicographic failure.
    expect(qtyCells()).toEqual(["9.5 m", "10 m", "100"]);

    await user.click(screen.getByRole("columnheader", { name: /qty/i }));
    expect(qtyCells()).toEqual(["100", "10 m", "9.5 m"]);
  });

  it("sorts a null quantity last regardless of direction", async () => {
    const user = userEvent.setup();
    render(
      <DataTable<Row>
        rows={[
          { id: "a", name: "A", qty: null, unit: "m" },
          { id: "b", name: "B", qty: 2.5, unit: "m" },
          { id: "c", name: "C", qty: 1.5, unit: "m" },
        ]}
        columns={COLUMNS}
        rowKey={r => r.id}
      />,
    );
    await user.click(screen.getByRole("columnheader", { name: /qty/i }));
    expect(qtyCells()).toEqual(["1.5 m", "2.5 m", ""]);
  });
});

describe("quantityColumn — CSV export stays numeric", () => {
  // jsdom's Blob has no `.text()`, so read it the long way round.
  function blobText(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(blob);
    });
  }

  async function exportedCsv(): Promise<string> {
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /export csv/i }));
    expect(capturedBlob).not.toBeNull();
    return await blobText(capturedBlob as unknown as Blob);
  }

  it("exports the raw number, not the unit-bearing display text", async () => {
    render(<DataTable<Row> rows={ROWS} columns={COLUMNS} rowKey={r => r.id} />);
    const csv = await exportedCsv();
    // The cell a spreadsheet can sum — no " m", no " pcs".
    expect(csv).toContain('"Wire","10"');
    expect(csv).toContain('"Paste","9.5"');
    expect(csv).toContain('"Resistor","100"');
    expect(csv).not.toContain("10 m");
  });

  it("exports a fractional quantity without truncating it", async () => {
    render(
      <DataTable<Row>
        rows={[{ id: "a", name: "Wire", qty: 12.5, unit: "m" }]}
        columns={COLUMNS}
        rowKey={r => r.id}
      />,
    );
    const csv = await exportedCsv();
    expect(csv).toContain('"Wire","12.5"');
    expect(csv).not.toContain('"Wire","12"');
  });

  it("exports a non-empty cell — the render-only-column trap", async () => {
    // A column with `render` but no `accessor` exports "" (DataTable's
    // `cellText`). Going through `quantityColumn` always sets both.
    render(<DataTable<Row> rows={ROWS} columns={COLUMNS} rowKey={r => r.id} />);
    const csv = await exportedCsv();
    expect(csv).not.toContain('"Wire",""');
  });

  it("exports in the current sort order", async () => {
    const user = userEvent.setup();
    render(<DataTable<Row> rows={ROWS} columns={COLUMNS} rowKey={r => r.id} />);
    await user.click(screen.getByRole("columnheader", { name: /qty/i }));
    const csv = await exportedCsv();
    const bodyRows = csv.split("\r\n").slice(1);
    expect(bodyRows).toEqual(['"Paste","9.5"', '"Wire","10"', '"Resistor","100"']);
  });
});

describe("quantityColumn — alignment and search", () => {
  it("right-aligns even when the first row's quantity is null", () => {
    // `defaultAlignFor` samples row 0, so an accessor-only column whose
    // first value is null would left-align while the rest of the table
    // right-aligns. `quantityColumn` pins align: "right".
    render(
      <DataTable<Row>
        rows={[
          { id: "a", name: "A", qty: null, unit: "m" },
          { id: "b", name: "B", qty: 5, unit: "m" },
        ]}
        columns={COLUMNS}
        rowKey={r => r.id}
      />,
    );
    const cell = within(screen.getAllByRole("row")[1]).getAllByRole("cell")[1];
    expect(cell.className).toContain("text-right");
    expect(cell.className).toContain("tabular-nums");
  });

  it("searches the raw number", async () => {
    const user = userEvent.setup();
    render(<DataTable<Row> rows={ROWS} columns={COLUMNS} rowKey={r => r.id} />);
    await user.type(screen.getByPlaceholderText("Search…"), "9.5");
    expect(qtyCells()).toEqual(["9.5 m"]);
  });
});
