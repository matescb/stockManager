// @vitest-environment jsdom
/**
 * Per-category spec columns on `/parts`.
 *
 * Four things are pinned, all of them things a reader of the component
 * cannot check by eye:
 *
 *  - the picker is driven by `GET /categories/{id}/spec-schema` and every
 *    toggle is a PATCH on the CATEGORY, not browser state — the whole
 *    point of the feature is that the choice is shared;
 *  - a spec column's header carries the unit and its cell carries the
 *    display value, so `Resistance (Ω)` / `10 kΩ` and not `10 kΩ Ω`;
 *  - a header click sorts on the SERVER (it reaches the request URL as
 *    `sort=spec:<key>`) rather than reordering the loaded page, because
 *    the list is cursor-paged and a client-side sort would only order the
 *    rows that happen to be in memory;
 *  - the saved default sort is offered but not assumed.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/instrument", () => ({}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ workspaceId: "ws-1" }) }));
vi.mock("@/components/ConfirmDialog", () => ({
  useConfirm: () => vi.fn(async () => false),
}));
vi.mock("@/routes/labels/BatchPrintDialog", () => ({ default: () => null }));

const CATEGORY_ID = "bbbbbbbb-2222-4222-8222-222222222222";
const PARENT_ID = "aaaaaaaa-1111-4111-8111-111111111111";

const CATEGORIES = [
  {
    id: PARENT_ID,
    name: "Passives",
    description: null,
    sort_order: 0,
    refdes_prefix: null,
    default_symbol_ref: null,
    default_footprint_ref: null,
    footprint_filters: null,
    value_template: null,
    kicad_fields: null,
    list_columns: null,
    list_sort: null,
    library_slug: "passives",
    parent_id: null,
    archived_at: null,
  },
  {
    id: CATEGORY_ID,
    name: "Resistors",
    description: null,
    sort_order: 0,
    refdes_prefix: null,
    default_symbol_ref: null,
    default_footprint_ref: null,
    footprint_filters: null,
    value_template: null,
    kicad_fields: null,
    list_columns: ["resistance"],
    list_sort: null,
    library_slug: "resistors",
    parent_id: PARENT_ID,
    archived_at: null,
  },
];

const SPEC_SCHEMA = {
  slug: "resistor",
  keys: [
    { key: "resistance", label: "Resistance", unit: "Ω", mandatory: true, numeric: true, common: false },
    { key: "tolerance", label: "Tolerance", unit: "%", mandatory: true, numeric: true, common: false },
    { key: "package", label: "Package", unit: null, mandatory: true, numeric: false, common: true },
    { key: "mounting", label: "Mounting", unit: null, mandatory: false, numeric: false, common: true },
  ],
  list_columns: ["resistance"],
  list_sort: null,
  inherited_from: null,
  sort_inherited_from: null,
};

const ROW = {
  id: "11111111-1111-4111-8111-111111111111",
  part_type: "local",
  name: "Resistor 10k",
  manufacturer: "Yageo",
  mpn: "RC0805FR-0710KL",
  internal_part_number: null,
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
  provider_links: [],
  specs: {
    resistance: { value: "10 kΩ", value_num: "10000" },
  },
};

// Mutable so a test can hand back a different schema (an inherited one, a
// saved sort) before rendering.
const state = vi.hoisted(() => ({
  schema: null as unknown,
  requestedUrls: [] as string[],
  patches: [] as { url: string; body: unknown }[],
}));

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
    getPaged: vi.fn((url: string) => {
      state.requestedUrls.push(url);
      return Promise.resolve({ items: [ROW], next_cursor: null });
    }),
    api: {
      get: vi.fn(() => Promise.resolve([])),
      parsed: {
        get: vi.fn((url: string) => {
          if (url.includes("/spec-schema")) return Promise.resolve(state.schema);
          return Promise.resolve(url.startsWith("/categories") ? CATEGORIES : []);
        }),
      },
      post: vi.fn(),
      patch: vi.fn((url: string, body: unknown) => {
        state.patches.push({ url, body });
        return Promise.resolve(null);
      }),
      delete: vi.fn(),
      upload: vi.fn(),
    },
  };
});

// Imported after the mocks so the module graph picks them up.
import PartsList from "../PartsList";

let currentSearch = "";

function LocationProbe() {
  currentSearch = useLocation().search;
  return null;
}

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
      <MemoryRouter initialEntries={[`/parts?category=${CATEGORY_ID}`]}>
        <LocationProbe />
        <Routes>
          <Route path="/parts" element={<PartsList />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("Resistor 10k");
}

function headers(): string[] {
  return Array.from(document.querySelectorAll("thead th")).map(th =>
    (th.textContent ?? "").trim(),
  );
}

function headerCell(startsWith: string): HTMLElement {
  const cell = Array.from(document.querySelectorAll("thead th")).find(th =>
    (th.textContent ?? "").trim().startsWith(startsWith),
  );
  expect(cell, `no column header starting with "${startsWith}"`).toBeTruthy();
  return cell as HTMLElement;
}

/** The row's `<td>` under a given column header. */
function cellUnder(startsWith: string): HTMLElement {
  const all = Array.from(document.querySelectorAll("thead th"));
  const index = all.findIndex(th =>
    (th.textContent ?? "").trim().startsWith(startsWith),
  );
  expect(index).toBeGreaterThanOrEqual(0);
  const row = document.querySelector("tbody tr") as HTMLElement;
  return row.querySelectorAll("td")[index] as HTMLElement;
}

function picker(): HTMLElement {
  const menu = screen.getByText("Spec columns").closest("details");
  expect(menu, "the Spec columns menu is missing").not.toBeNull();
  return menu as HTMLElement;
}

function lastPartsUrl(): string {
  return state.requestedUrls[state.requestedUrls.length - 1];
}

beforeEach(() => {
  localStorage.clear();
  stubMatchMedia();
  state.schema = SPEC_SCHEMA;
  state.requestedUrls = [];
  state.patches = [];
});
afterEach(cleanup);

describe("PartsList — per-category spec columns", () => {
  it("asks for the configured keys and renders their values", async () => {
    await renderList();
    await waitFor(() => expect(lastPartsUrl()).toContain("spec_columns=resistance"));

    // The unit is in the header, once, not repeated in every cell.
    expect(headers()).toContain("Resistance (Ω)");
    expect(cellUnder("Resistance").textContent).toBe("10 kΩ");
  });

  it("does not offer spec columns until a category is selected", async () => {
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
    expect(screen.queryByText("Spec columns")).toBeNull();
    expect(lastPartsUrl()).not.toContain("spec_columns");
  });

  it("lists the schema's keys mandatory-first, with units", async () => {
    await renderList();
    const labels = Array.from(
      within(picker()).getAllByRole("checkbox"),
    ).map(box => (box.closest("label")?.textContent ?? "").trim());
    // Mandatory keys first, each group in schema order — `Mounting` is the
    // only optional one and lands last even though the schema lists it
    // among the common keys.
    expect(labels.map(l => l.replace("required", "").trim())).toEqual([
      "Resistance (Ω)",
      "Tolerance (%)",
      "Package",
      "Mounting",
    ]);
  });

  it("saves a toggled column on the category, appended to the stored order", async () => {
    await renderList();
    fireEvent.click(within(picker()).getByLabelText(/^Tolerance/));

    await waitFor(() => expect(state.patches).toHaveLength(1));
    expect(state.patches[0].url).toBe(`/categories/${CATEGORY_ID}`);
    // Appended, not re-sorted into schema order: the stored list IS the
    // column order.
    expect(state.patches[0].body).toEqual({
      list_columns: ["resistance", "tolerance"],
    });
  });

  it("unticking a column removes just that key", async () => {
    await renderList();
    fireEvent.click(within(picker()).getByLabelText(/^Resistance/));
    await waitFor(() => expect(state.patches).toHaveLength(1));
    expect(state.patches[0].body).toEqual({ list_columns: [] });
  });

  it("says which ancestor the column choice came from", async () => {
    state.schema = { ...SPEC_SCHEMA, inherited_from: PARENT_ID };
    await renderList();
    expect(within(picker()).getByText(/Inherited from Passives/)).toBeTruthy();
  });

  it("a spec header click sorts on the server and rides the URL", async () => {
    await renderList();
    fireEvent.click(headerCell("Resistance"));

    await waitFor(() => expect(currentSearch).toContain("sort=resistance"));
    await waitFor(() =>
      expect(lastPartsUrl()).toContain("sort=spec%3Aresistance&dir=asc"),
    );

    // A second click flips the direction rather than adding a second sort.
    fireEvent.click(headerCell("Resistance"));
    await waitFor(() => expect(currentSearch).toContain("dir=desc"));
    await waitFor(() => expect(lastPartsUrl()).toContain("&dir=desc"));
  });

  it("offers the active sort as the category default, then saves it", async () => {
    await renderList();
    expect(screen.queryByText("Save as default sort")).toBeNull();

    fireEvent.click(headerCell("Resistance"));
    const save = await screen.findByText("Save as default sort");
    fireEvent.click(save);

    await waitFor(() => expect(state.patches).toHaveLength(1));
    expect(state.patches[0].body).toEqual({
      list_sort: { key: "resistance", dir: "asc" },
    });
  });

  it("does not offer to save a sort that is already the default", async () => {
    state.schema = { ...SPEC_SCHEMA, list_sort: { key: "resistance", dir: "asc" } };
    await renderList();
    // The saved default is applied by the SERVER, so the URL carries no
    // sort — but the header still has to show which way the list is going.
    expect(currentSearch).not.toContain("sort=");
    expect(headerCell("Resistance").textContent).toContain("▲");
    expect(screen.queryByText("Save as default sort")).toBeNull();
  });

  it("drops the sort when the category changes", async () => {
    await renderList();
    fireEvent.click(headerCell("Resistance"));
    await waitFor(() => expect(currentSearch).toContain("sort=resistance"));

    // The narrow-screen category `<select>`; a spec key belongs to one
    // category's schema, so carrying it over would 422 the listing.
    fireEvent.change(screen.getByLabelText("Category"), {
      target: { value: PARENT_ID },
    });
    await waitFor(() => expect(currentSearch).not.toContain("sort="));
  });

  it("exports and hides spec columns like any other column", async () => {
    await renderList();
    const columnsMenu = screen.getByText("Columns").closest("details") as HTMLElement;
    const toggle = within(columnsMenu).getByLabelText("Resistance (Ω)");
    expect((toggle as HTMLInputElement).checked).toBe(true);

    fireEvent.click(toggle);
    await waitFor(() => expect(headers()).not.toContain("Resistance (Ω)"));
    // No PATCH: the Columns menu is per-viewer visibility, not the
    // workspace-wide column choice.
    expect(state.patches).toHaveLength(0);
  });
});
