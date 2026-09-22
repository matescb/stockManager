/**
 * `extraColumnSections` — the Columns menu's extension point.
 *
 * The parts list needs a second kind of column toggle in the same menu:
 * its per-category spec columns, where a tick is a PATCH on the category
 * rather than a per-viewer hide. Both kinds have to be in the one menu,
 * because that is where a user looks for a column — but they must not be
 * mixed, and the same column must never get two checkboxes that mean
 * different things.
 *
 * So what is pinned here is the contract, not the parts list:
 *
 *  - a section renders its title, note, items and footer;
 *  - an item's `key` claims the matching column out of the table's OWN
 *    list, so it appears once;
 *  - a claimed column is still rendered in the table — claiming governs
 *    the menu, not visibility;
 *  - an empty section says so rather than rendering a bare heading.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { DataTable, type ColumnMenuSection } from "../DataTable";

type Row = { id: string; name: string; ohms: string };

const ROWS: Row[] = [{ id: "a", name: "R 10k", ohms: "10 kΩ" }];

const COLUMNS = [
  { key: "name", header: "Name", accessor: (r: Row) => r.name },
  { key: "spec:resistance", header: "Resistance (Ω)", accessor: (r: Row) => r.ohms },
];

function menu(): HTMLElement {
  return screen.getByText("Columns").closest("details") as HTMLElement;
}

function labels(): string[] {
  return within(menu())
    .getAllByRole("checkbox")
    .map(box => (box.closest("label")?.textContent ?? "").trim());
}

function renderTable(section: ColumnMenuSection | null) {
  render(
    <DataTable
      rows={ROWS}
      columns={COLUMNS}
      rowKey={r => r.id}
      extraColumnSections={section ? [section] : undefined}
    />,
  );
}

beforeEach(cleanup);

describe("DataTable — extra Columns-menu sections", () => {
  it("renders a section's title, note, items and footer", () => {
    renderTable({
      id: "specs",
      title: "Specs",
      note: "Inherited from Passives.",
      footer: "At most 12 spec columns.",
      items: [
        {
          key: "spec:resistance",
          label: "Resistance (Ω)",
          checked: true,
          badge: "required",
          onToggle: () => {},
        },
      ],
    });

    expect(within(menu()).getByText("Specs")).toBeTruthy();
    expect(within(menu()).getByText("Inherited from Passives.")).toBeTruthy();
    expect(within(menu()).getByText("At most 12 spec columns.")).toBeTruthy();
    expect(labels()).toEqual(["Name", "Resistance (Ω)required"]);
  });

  it("claims its own columns out of the table's per-viewer list", () => {
    renderTable({
      id: "specs",
      title: "Specs",
      items: [
        { key: "spec:resistance", label: "Resistance (Ω)", checked: true, onToggle: () => {} },
        // An available key with no column — unticked, so nothing to claim.
        { key: "spec:tolerance", label: "Tolerance (%)", checked: false, onToggle: () => {} },
      ],
    });

    // Once, from the section — not twice.
    expect(labels().filter(l => l.startsWith("Resistance"))).toHaveLength(1);
    // …and the column itself is untouched by the claim.
    expect(screen.getByText("10 kΩ")).toBeTruthy();
  });

  it("calls the item's own handler instead of hiding the column", () => {
    const onToggle = vi.fn();
    renderTable({
      id: "specs",
      title: "Specs",
      items: [
        { key: "spec:resistance", label: "Resistance (Ω)", checked: true, onToggle },
      ],
    });

    fireEvent.click(within(menu()).getByLabelText("Resistance (Ω)"));
    expect(onToggle).toHaveBeenCalledTimes(1);
    // The table did not hide it behind the caller's back: the caller owns
    // what a tick means, and here it means "tell the server".
    expect(screen.getByText("10 kΩ")).toBeTruthy();
  });

  it("says a section is empty rather than showing a bare heading", () => {
    renderTable({
      id: "specs",
      title: "Specs",
      items: [],
      emptyNote: "This category has no spec keys.",
    });

    expect(within(menu()).getByText("This category has no spec keys.")).toBeTruthy();
    expect(labels()).toEqual(["Name", "Resistance (Ω)"]);
  });

  it("leaves the menu exactly as it was without a section", () => {
    renderTable(null);
    expect(labels()).toEqual(["Name", "Resistance (Ω)"]);
    expect(within(menu()).queryByText("Specs")).toBeNull();
  });
});
