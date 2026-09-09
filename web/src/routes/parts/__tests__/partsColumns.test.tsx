// @vitest-environment jsdom
/**
 * The `/parts` column set.
 *
 * Three classes of bug live here, none of which a type signature catches:
 *
 *  1. **A render-only column exports an empty CSV cell.** `DataTable`'s
 *     `cellText` has no safe way to pull text out of an arbitrary
 *     `ReactNode`, so a column with a `render` and no `accessor` silently
 *     ships blank columns to every spreadsheet.
 *  2. **A formatted accessor sorts as text.** `"10 pcs"` lands before
 *     `"9 pcs"`, and a date rendered `MM/DD/YYYY` sorts by month.
 *  3. **A column that was visible becomes hidden.** Shipping the new
 *     columns hidden is deliberate; hiding the ones that were already on
 *     screen would be a regression, and the two changes look identical in
 *     a diff of the same array.
 */
import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Column } from "@/components/DataTable";
import type { Part, PartCategory } from "@/lib/schemas";
import { partsListColumns } from "../partsColumns";

const PART_ID = "11111111-1111-4111-8111-111111111111";
const PARENT_CATEGORY_ID = "aaaaaaaa-1111-4111-8111-111111111111";
const LEAF_CATEGORY_ID = "bbbbbbbb-2222-4222-8222-222222222222";

function makeCategory(over: Partial<PartCategory> & { id: string; name: string }): PartCategory {
  return {
    description: null,
    sort_order: 0,
    refdes_prefix: null,
    default_symbol_ref: null,
    default_footprint_ref: null,
    footprint_filters: null,
    library_slug: over.name.toLowerCase(),
    parent_id: null,
    archived_at: null,
    ...over,
  } as PartCategory;
}

const CATEGORIES: PartCategory[] = [
  makeCategory({ id: PARENT_CATEGORY_ID, name: "Passives" }),
  makeCategory({
    id: LEAF_CATEGORY_ID,
    name: "Resistors",
    parent_id: PARENT_CATEGORY_ID,
  }),
];

function makePart(over: Partial<Part> = {}): Part {
  return {
    id: PART_ID,
    part_type: "local",
    name: "Resistor 10k",
    manufacturer: "Yageo",
    mpn: "RC0805FR-0710KL",
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
    category_id: null,
    published: false,
    linked_provider: null,
    linked_external_id: null,
    last_refresh_at: null,
    updated_at: null,
    description_locally_edited: false,
    archived_at: null,
    on_hand: 0,
    reserved: 0,
    available: 0,
    image_url: null,
    ...over,
  } as Part;
}

const columns = () => partsListColumns({ categories: CATEGORIES });

function column(key: string): Column<Part> {
  const found = columns().find(c => c.key === key);
  if (!found) throw new Error(`no column with key "${key}"`);
  return found;
}

function accessorOf(key: string, row: Part) {
  const acc = column(key).accessor;
  if (!acc) throw new Error(`column "${key}" has no accessor`);
  return acc(row);
}

function renderCell(key: string, row: Part) {
  const col = column(key);
  if (!col.render) throw new Error(`column "${key}" has no render`);
  return render(<>{col.render(row)}</>);
}

afterEach(cleanup);

describe("partsListColumns — the set", () => {
  it("offers every field the list payload carries", () => {
    expect(columns().map(c => c.key)).toEqual([
      "image",
      "part_type",
      "name",
      "mpn",
      "internal_part_number",
      "manufacturer",
      "footprint",
      "category",
      "on_hand",
      "reserved",
      "available",
      "low_stock_report_quantity",
      "linked_provider",
      "provider_links",
      "published",
      "serialized",
      "last_refresh_at",
      "updated_at",
    ]);
  });

  it("keeps the columns that were already on screen visible", () => {
    const visible = columns()
      .filter(c => !c.hidden)
      .map(c => c.key);
    expect(visible).toEqual([
      "image",
      "part_type",
      "name",
      "mpn",
      "manufacturer",
      "footprint",
      "on_hand",
    ]);
  });

  it("ships every optional column hidden, so nothing arrives switched on", () => {
    const hidden = columns()
      .filter(c => c.hidden)
      .map(c => c.key);
    expect(hidden).toContain("internal_part_number");
    expect(hidden).toContain("category");
    expect(hidden).toContain("available");
    expect(hidden).toContain("low_stock_report_quantity");
    expect(hidden).toContain("linked_provider");
    expect(hidden).toContain("provider_links");
    expect(hidden).toContain("published");
    expect(hidden).toContain("serialized");
    expect(hidden).toContain("last_refresh_at");
    expect(hidden).toContain("updated_at");
  });

  it("gives every column but the thumbnail an accessor, so CSV is never blank", () => {
    const withoutAccessor = columns()
      .filter(c => !c.accessor)
      .map(c => c.key);
    expect(withoutAccessor).toEqual(["image"]);
  });
});

describe("partsListColumns — accessors", () => {
  it("reads the plain string fields, blanking null rather than printing it", () => {
    const row = makePart({ internal_part_number: "IPN-0001", footprint: "R_0402" });
    expect(accessorOf("internal_part_number", row)).toBe("IPN-0001");
    expect(accessorOf("footprint", row)).toBe("R_0402");
    expect(accessorOf("internal_part_number", makePart())).toBe("");
  });

  it("renders the category as its full path, not the leaf name", () => {
    const row = makePart({ category_id: LEAF_CATEGORY_ID });
    expect(accessorOf("category", row)).toBe("Passives / Resistors");
    expect(accessorOf("category", makePart())).toBe("");
  });

  it("shows the provider's display name, not its wire value", () => {
    expect(accessorOf("linked_provider", makePart({ linked_provider: "digikey" }))).toBe(
      "DigiKey",
    );
    expect(accessorOf("linked_provider", makePart())).toBe("");
  });

  it("spells booleans out so a CSV reads as words", () => {
    expect(accessorOf("published", makePart({ published: true }))).toBe("Yes");
    expect(accessorOf("published", makePart({ published: false }))).toBe("No");
    expect(accessorOf("serialized", makePart({ serialized: true }))).toBe("Yes");
  });

  it("keeps quantity accessors numeric so 10 sorts after 9", () => {
    const nine = makePart({ on_hand: 9, reserved: 9, available: 9 });
    const ten = makePart({ on_hand: 10, reserved: 10, available: 10 });
    for (const key of ["on_hand", "reserved", "available"]) {
      expect(typeof accessorOf(key, ten)).toBe("number");
      expect(Number(accessorOf(key, ten))).toBeGreaterThan(Number(accessorOf(key, nine)));
    }
  });

  it("leaves an unset low-stock threshold blank rather than showing it as zero", () => {
    expect(accessorOf("low_stock_report_quantity", makePart())).toBe(null);
    renderCell("low_stock_report_quantity", makePart());
    expect(document.body.textContent).toBe("");

    cleanup();
    renderCell("low_stock_report_quantity", makePart({ low_stock_report_quantity: 25 }));
    expect(document.body.textContent).toBe("25");
  });

  it("sorts dates chronologically — the accessor is YYYY-MM-DD, not a locale format", () => {
    const older = makePart({ updated_at: "2026-01-02T09:00:00Z" });
    const newer = makePart({ updated_at: "2026-11-30T09:00:00Z" });
    expect(accessorOf("updated_at", older)).toBe("2026-01-02");
    expect(accessorOf("updated_at", newer)).toBe("2026-11-30");
    // Lexicographic order is chronological order for this shape. A
    // `MM/DD/YYYY` accessor would put 11-30 *before* 01-02.
    expect(String(accessorOf("updated_at", older)) < String(accessorOf("updated_at", newer)))
      .toBe(true);
    expect(accessorOf("updated_at", makePart())).toBe("");
    expect(accessorOf("last_refresh_at", makePart({ last_refresh_at: "2026-03-15T10:30:00Z" })))
      .toBe("2026-03-15");
  });
});

describe("partsListColumns — distributor links", () => {
  const linked = makePart({
    provider_links: [
      {
        provider: "digikey",
        external_id: "DK-1",
        source_url: "https://www.digikey.com/en/products/detail/x/1",
        last_refresh_at: null,
      },
      {
        provider: "mouser",
        external_id: "MO-1",
        source_url: "https://www.mouser.com/ProductDetail/x",
        last_refresh_at: null,
      },
    ],
  });

  it("exports and searches on the distributor names", () => {
    expect(accessorOf("provider_links", linked)).toBe("DigiKey, Mouser");
  });

  it("links out to each distributor's own page", () => {
    renderCell("provider_links", linked);
    const digikey = screen.getByRole("link", { name: "DigiKey" });
    expect(digikey.getAttribute("href")).toBe(
      "https://www.digikey.com/en/products/detail/x/1",
    );
    expect(digikey.getAttribute("target")).toBe("_blank");
    // Without noopener the opened tab gets a handle on this one.
    expect(digikey.getAttribute("rel")).toBe("noopener noreferrer");
    expect(screen.getByRole("link", { name: "Mouser" })).toBeTruthy();
  });

  it("refuses to render a non-http URL as a link", () => {
    const hostile = makePart({
      provider_links: [
        {
          provider: "mouser",
          // eslint-disable-next-line no-script-url
          source_url: "javascript:alert(1)",
          external_id: null,
          last_refresh_at: null,
        },
      ],
    });
    renderCell("provider_links", hostile);
    expect(screen.queryByRole("link")).toBeNull();
    // The name still shows — the row does know Mouser has this part.
    expect(document.body.textContent).toContain("Mouser");
  });

  it("is blank for a part no distributor knows", () => {
    expect(accessorOf("provider_links", makePart({ provider_links: [] }))).toBe("");
    renderCell("provider_links", makePart({ provider_links: [] }));
    expect(document.body.textContent).toBe("");
  });

  it("treats a row the server never loaded links for as blank, not as an error", () => {
    // `provider_links` is optional on the wire: absent means "not
    // loaded". Lists do load it, but detail-shaped fixtures and cached
    // responses predate that.
    expect(accessorOf("provider_links", makePart())).toBe("");
  });
});
