/**
 * The parts list's column set, as wired into the route.
 *
 * `partsColumns.test.tsx` pins what each column *is*; this pins that the
 * table actually uses them, and — the part users feel — that the new ones
 * arrive **off**. Eighteen columns switched on by default would be a wall
 * of data, and `DataTable` merges the persisted per-workspace map over
 * the declared defaults, so "hidden by default" also means "unless you
 * turned it on".
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Part } from "@/lib/schemas";

vi.mock("@/instrument", () => ({}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ workspaceId: "ws-1" }) }));
vi.mock("@/components/ConfirmDialog", () => ({
  useConfirm: () => vi.fn(async () => false),
}));
vi.mock("@/routes/labels/BatchPrintDialog", () => ({ default: () => null }));

const PART_ID = "11111111-1111-4111-8111-111111111111";
const CATEGORY_ID = "44444444-4444-4444-8444-444444444444";

const CATEGORIES = [
  {
    id: CATEGORY_ID,
    name: "Passives",
    description: null,
    sort_order: 0,
    refdes_prefix: null,
    default_symbol_ref: null,
    default_footprint_ref: null,
    footprint_filters: null,
    library_slug: "passives",
    parent_id: null,
    archived_at: null,
  },
];

const ROW = {
  id: PART_ID,
  part_type: "local",
  name: "Resistor 10k",
  manufacturer: "Yageo",
  mpn: "RC0805FR-0710KL",
  internal_part_number: "IPN-0042",
  description: null,
  footprint: "R_0805",
  notes_markdown: null,
  low_stock_report_quantity: null,
  attrition_percentage: 0,
  attrition_min_quantity: 0,
  default_storage_location_id: null,
  default_storage_mandatory: false,
  serialized: false,
  category_id: CATEGORY_ID,
  published: false,
  linked_provider: null,
  linked_external_id: null,
  last_refresh_at: null,
  updated_at: "2026-03-15T10:30:00Z",
  description_locally_edited: false,
  archived_at: null,
  on_hand: 1200,
  reserved: 0,
  available: 1200,
  image_url: null,
  provider_links: [
    {
      provider: "digikey",
      external_id: "DK-1",
      source_url: "https://www.digikey.com/en/products/detail/x/1",
      last_refresh_at: null,
    },
  ],
} as unknown as Part;

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    body: unknown;
    userMessage: string;
    constructor(status: number, body: unknown, msg = "api error") {
      super(msg);
      this.status = status;
      this.body = body;
      this.userMessage = msg;
    }
  }
  return {
    ApiError,
    getPaged: vi.fn(() => Promise.resolve({ items: [ROW], next_cursor: null })),
    api: {
      get: vi.fn(() => Promise.resolve([])),
      parsed: {
        get: vi.fn((url: string) =>
          Promise.resolve(url.startsWith("/categories") ? CATEGORIES : []),
        ),
      },
      post: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
      upload: vi.fn(),
    },
  };
});

// Imported after the mocks so the module graph picks them up.
import PartsList from "../PartsList";

/** jsdom has no matchMedia; the narrow branch is the honest default. */
function stubMatchMedia() {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

async function renderList() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/parts"]}>
        <Routes>
          <Route path="/parts" element={<PartsList />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("Resistor 10k");
}

/** The row's `<td>` under a given column header. */
function cellUnder(header: string): HTMLElement {
  const headers = Array.from(document.querySelectorAll("thead th"));
  const index = headers.findIndex(th => th.textContent?.startsWith(header));
  expect(index, `no column header starting with "${header}"`).toBeGreaterThanOrEqual(0);
  const row = document.querySelector("tbody tr") as HTMLElement;
  return row.querySelectorAll("td")[index] as HTMLElement;
}

/**
 * The "Columns" dropdown. Scoped rather than queried globally: the
 * narrow-screen category filter is also labelled "Category", and a bare
 * `getByLabelText` matches both.
 */
function columnsMenu(): HTMLElement {
  const menu = screen.getByText("Columns").closest("details");
  expect(menu, "the Columns menu is missing").not.toBeNull();
  return menu as HTMLElement;
}

/** Tick a column on via the "Columns" menu. */
function showColumn(label: string) {
  fireEvent.click(within(columnsMenu()).getByLabelText(label));
}

beforeEach(() => {
  localStorage.clear();
  stubMatchMedia();
});
afterEach(cleanup);

describe("PartsList — column set", () => {
  it("starts with the short table, not with all eighteen columns", async () => {
    await renderList();
    const headers = Array.from(document.querySelectorAll("thead th")).map(th =>
      (th.textContent ?? "").trim(),
    );
    expect(headers).toEqual([
      "", // select-all checkbox
      "", // thumbnail
      "Type",
      "Part",
      "MPN",
      "Manufacturer",
      "Footprint",
      "Stock",
    ]);
  });

  it("offers every new column in the Columns menu", async () => {
    await renderList();
    for (const label of [
      "Internal P/N",
      "Category",
      "Available",
      "Low-stock at",
      "Provider",
      "Distributors",
      "Published",
      "Serialized",
      "Last refresh",
      "Last change",
    ]) {
      expect(
        within(columnsMenu()).getByLabelText(label),
        `"${label}" missing from the Columns menu`,
      ).toBeTruthy();
    }
  });

  it("shows the last-change date once the column is switched on", async () => {
    await renderList();
    showColumn("Last change");
    await waitFor(() => expect(cellUnder("Last change").textContent).toBe("2026-03-15"));
  });

  it("resolves the category id to its name client-side", async () => {
    await renderList();
    showColumn("Category");
    await waitFor(() => expect(cellUnder("Category").textContent).toBe("Passives"));
  });

  it("links out to the distributor's own page", async () => {
    await renderList();
    showColumn("Distributors");
    await waitFor(() => {
      const link = within(cellUnder("Distributors")).getByRole("link", { name: "DigiKey" });
      expect(link.getAttribute("href")).toBe(
        "https://www.digikey.com/en/products/detail/x/1",
      );
    });
  });

  it("keeps a switched-on column on across a remount", async () => {
    await renderList();
    showColumn("Internal P/N");
    await waitFor(() => expect(cellUnder("Internal P/N").textContent).toBe("IPN-0042"));

    cleanup();
    await renderList();
    await waitFor(() => expect(cellUnder("Internal P/N").textContent).toBe("IPN-0042"));
  });
});
