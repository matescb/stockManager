// @vitest-environment jsdom
/**
 * Per-category spec columns on `/parts`.
 *
 * Six things are pinned, all of them things a reader of the component
 * cannot check by eye:
 *
 *  - the spec keys live in the table's own Columns menu, in a "Specs"
 *    section, because that is where everyone looks for a column — a
 *    picker of its own in the category bar went unnoticed on prod;
 *  - that section is driven by `GET /categories/{id}/spec-schema` and
 *    every toggle is a PATCH on the CATEGORY, not browser state — the
 *    whole point of the feature is that the choice is shared, which is
 *    also why a spec key is NOT in the table's per-viewer list;
 *  - a spec column's header carries the unit and its cell carries the
 *    display value, so `Resistance (Ω)` / `10 kΩ` and not `10 kΩ Ω`;
 *  - a header click sorts on the SERVER (it reaches the request URL as
 *    `sort=spec:<key>`) rather than reordering the loaded page, because
 *    the list is cursor-paged and a client-side sort would only order the
 *    rows that happen to be in memory;
 *  - the saved default sort is offered but not assumed;
 *  - a stored key the schema no longer has is dropped from the request,
 *    from the table AND from the next PATCH's payload — otherwise a
 *    category renamed away from its schema turns an unrelated tick into a
 *    422.
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

function key(
  k: string,
  label: string,
  unit: string | null,
  mandatory: boolean,
  common: boolean,
  slugs: string[] = ["resistor"],
) {
  return { key: k, label, unit, mandatory, numeric: unit !== null, common, slugs };
}

const SPEC_SCHEMA = {
  slug: "resistor",
  class: "resistor",
  keys: [
    key("resistance", "Resistance", "Ω", true, false),
    key("tolerance", "Tolerance", "%", true, false),
    key("package", "Package", null, true, true),
    key("mounting", "Mounting", null, false, true),
  ],
  list_columns: ["resistance"],
  list_sort: null,
  inherited_from: null,
  sort_inherited_from: null,
};

/** A bare "Capacitors" root: no slug, a class, and the union of its keys. */
const UNION_SCHEMA = {
  slug: null,
  class: "capacitor",
  keys: [
    key("package", "Package", null, true, true, [
      "capacitor_ceramic",
      "capacitor_electrolytic",
    ]),
    key("capacitance", "Capacitance", "F", true, false, [
      "capacitor_ceramic",
      "capacitor_electrolytic",
    ]),
    key("dielectric", "Dielectric", null, false, false, ["capacitor_ceramic"]),
    key("esr", "ESR", "Ω", false, false, ["capacitor_electrolytic"]),
  ],
  list_columns: [],
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
  // With this set, a `spec-schema` GET hangs until `releaseSchema()` is
  // called. That gap — PATCH resolved, schema not yet back — is where a
  // second toggle used to build its payload from a stale column list.
  deferSchema: false,
  pendingSchema: [] as (() => void)[],
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
          if (url.includes("/spec-schema")) {
            if (!state.deferSchema) return Promise.resolve(state.schema);
            return new Promise(resolve => {
              state.pendingSchema.push(() => resolve(state.schema));
            });
          }
          return Promise.resolve(url.startsWith("/categories") ? CATEGORIES : []);
        }),
      },
      post: vi.fn(),
      patch: vi.fn((url: string, body: unknown) => {
        state.patches.push({ url, body });
        // The server stores what it was sent, so the next `spec-schema`
        // read answers with it. Without this the refetch would hand back
        // the original list and the race would be invisible.
        const columns = (body as { list_columns?: string[] }).list_columns;
        if (columns) {
          state.schema = { ...(state.schema as object), list_columns: columns };
        }
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

/** The table's Columns menu — which is where the spec toggles now live. */
function columnsMenu(): HTMLElement {
  const menu = screen.getByText("Columns").closest("details");
  expect(menu, "the Columns menu is missing").not.toBeNull();
  return menu as HTMLElement;
}

/** The "Specs" section inside it. */
function specsSection(): HTMLElement {
  const heading = within(columnsMenu()).queryByText("Specs");
  expect(heading, "the Columns menu has no Specs section").not.toBeNull();
  return (heading as HTMLElement).parentElement as HTMLElement;
}

/** The labels of the Specs section's checkboxes, badges stripped. */
function specLabels(): string[] {
  return Array.from(within(specsSection()).getAllByRole("checkbox")).map(box =>
    (box.closest("label")?.textContent ?? "").trim(),
  );
}

/** One spec toggle, found by the label it starts with. */
function specToggle(startsWith: string): HTMLInputElement {
  const box = Array.from(
    within(specsSection()).getAllByRole("checkbox"),
  ).find(input =>
    (input.closest("label")?.textContent ?? "").trim().startsWith(startsWith),
  );
  expect(box, `no spec toggle starting with "${startsWith}"`).toBeTruthy();
  return box as HTMLInputElement;
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
  state.deferSchema = false;
  state.pendingSchema = [];
});

function releaseSchema() {
  const waiting = state.pendingSchema;
  state.pendingSchema = [];
  for (const resolve of waiting) resolve();
}
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
    // The Columns menu is still there; its Specs section is not.
    expect(within(columnsMenu()).queryByText("Specs")).toBeNull();
    expect(lastPartsUrl()).not.toContain("spec_columns");
  });

  it("lists the schema's keys mandatory-first, with units", async () => {
    await renderList();
    // Mandatory keys first, each group in schema order — `Mounting` is the
    // only optional one and lands last even though the schema lists it
    // among the common keys.
    expect(specLabels().map(l => l.replace("required", "").trim())).toEqual([
      "Resistance (Ω)",
      "Tolerance (%)",
      "Package",
      "Mounting",
    ]);
  });

  it("keeps the spec keys out of the table's own per-viewer list", async () => {
    // Two checkboxes for one column would mean two different things — a
    // per-browser hide and a workspace-wide removal — and a user who used
    // the wrong one could not get the column back.
    await renderList();
    const all = within(columnsMenu())
      .getAllByRole("checkbox")
      .map(box => (box.closest("label")?.textContent ?? "").trim());
    expect(all.filter(l => l.startsWith("Resistance (Ω)"))).toHaveLength(1);
  });

  it("offers a bare root's whole class union, badged by subtype", async () => {
    // The bug this fixes: selecting the root "Capacitors" showed no spec
    // columns at all, although the parts under it carry `capacitance`.
    state.schema = UNION_SCHEMA;
    await renderList();

    expect(specLabels().map(l => l.replace("required", "").trim())).toEqual([
      "Package",
      "Capacitance (F)",
      // The subtype badge: `dielectric` is not an electrolytic's key and
      // `esr` is not a ceramic's, and the union has to say so.
      "Dielectricceramic",
      "ESR (Ω)electrolytic",
    ]);
  });

  it("saves a toggled column on the category, appended to the stored order", async () => {
    await renderList();
    fireEvent.click(specToggle("Tolerance"));

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
    fireEvent.click(specToggle("Resistance"));
    await waitFor(() => expect(state.patches).toHaveLength(1));
    expect(state.patches[0].body).toEqual({ list_columns: [] });
  });

  it("fetches the list once, with the columns already known", async () => {
    // Firing before the schema lands costs a page without `spec_columns`
    // and then the same page with it — two requests for one render, on
    // the busiest endpoint in the app.
    await renderList();
    await waitFor(() => expect(lastPartsUrl()).toContain("spec_columns=resistance"));
    expect(state.requestedUrls).toHaveLength(1);
  });

  it("drops a `?sort=` key this category's schema does not have", async () => {
    // A stale link, or a shared URL whose category was changed underneath
    // it. Forwarding the key would 422 the whole listing.
    const client = new QueryClient({
      defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter
          initialEntries={[`/parts?category=${CATEGORY_ID}&sort=dielectric`]}
        >
          <LocationProbe />
          <Routes>
            <Route path="/parts" element={<PartsList />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByText("Resistor 10k");
    expect(lastPartsUrl()).not.toContain("sort=");
    // And a key the schema DOES have still goes through.
    expect(lastPartsUrl()).toContain("spec_columns=resistance");
  });

  it("drops a stored key the schema no longer has, everywhere", async () => {
    // A category renamed away from its schema keeps the stored key.
    // Sending it back would 422 the whole PATCH, so an unrelated tick must
    // not carry it — and the table must not render a column for it either.
    state.schema = {
      ...SPEC_SCHEMA,
      list_columns: ["resistance", "dielectric"],
    };
    await renderList();
    await waitFor(() => expect(lastPartsUrl()).toContain("spec_columns=resistance"));
    expect(lastPartsUrl()).not.toContain("dielectric");
    expect(headers()).not.toContain("Dielectric");

    fireEvent.click(specToggle("Tolerance"));
    await waitFor(() => expect(state.patches).toHaveLength(1));
    expect(state.patches[0].body).toEqual({
      list_columns: ["resistance", "tolerance"],
    });
  });

  it("says which ancestor the column choice came from", async () => {
    state.schema = { ...SPEC_SCHEMA, inherited_from: PARENT_ID };
    await renderList();
    expect(
      within(specsSection()).getByText(/Inherited from Passives/),
    ).toBeTruthy();
  });

  it("keeps the toggles disabled until the saved schema is back", async () => {
    // The PATCH resolving is not the end of the save: until the
    // `spec-schema` query has refetched, the section is still rendering
    // the OLD column list. A second toggle in that window would build its
    // payload from it and silently undo the first one.
    await renderList();
    state.deferSchema = true;

    fireEvent.click(specToggle("Tolerance"));
    await waitFor(() => expect(state.patches).toHaveLength(1));
    await waitFor(() => expect(specToggle("Package").disabled).toBe(true));

    releaseSchema();
    await waitFor(() => expect(specToggle("Package").disabled).toBe(false));
  });

  it("a second toggle builds on the first one's result", async () => {
    await renderList();
    state.deferSchema = true;

    fireEvent.click(specToggle("Tolerance"));
    await waitFor(() => expect(state.patches).toHaveLength(1));
    releaseSchema();
    await waitFor(() => expect(specToggle("Package").disabled).toBe(false));

    fireEvent.click(specToggle("Package"));
    await waitFor(() => expect(state.patches).toHaveLength(2));
    // All three, not `["resistance", "package"]` — the second payload has
    // to carry the first toggle's key.
    expect(state.patches[1].body).toEqual({
      list_columns: ["resistance", "tolerance", "package"],
    });
  });

  it("caps the choice at twelve keys and says so", async () => {
    // The server refuses a thirteenth with a 422; disabling the unticked
    // ones turns that into a sentence rather than an error toast.
    const many = Array.from({ length: 12 }, (_, i) => `k${i}`);
    state.schema = {
      ...SPEC_SCHEMA,
      keys: [
        ...SPEC_SCHEMA.keys,
        ...many.map(k => key(k, k.toUpperCase(), null, false, false)),
      ],
      list_columns: many,
    };
    await renderList();

    expect(
      within(specsSection()).getByText(/At most 12 spec columns/),
    ).toBeTruthy();
    // An unticked key cannot be added; a ticked one can still be removed.
    expect(specToggle("Resistance").disabled).toBe(true);
    expect(specToggle("K0").disabled).toBe(false);
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

  it("unticking a spec column in the menu removes it for everyone", async () => {
    // The fixed part columns keep their per-viewer `localStorage`
    // visibility; a spec column is the category's choice, so the same
    // menu does a different thing on that half of it — deliberately.
    await renderList();
    const toggle = specToggle("Resistance");
    expect(toggle.checked).toBe(true);

    fireEvent.click(toggle);
    await waitFor(() => expect(state.patches).toHaveLength(1));
    expect(state.patches[0].body).toEqual({ list_columns: [] });

    // The save re-reads the schema and re-requests the page without the
    // column, so wait for the table to come back before touching it.
    await waitFor(() => expect(headers()).not.toContain("Resistance (Ω)"));

    // …while a fixed column is still a local hide, with no request.
    fireEvent.click(within(columnsMenu()).getByLabelText("Manufacturer"));
    await waitFor(() => expect(headers()).not.toContain("Manufacturer"));
    expect(state.patches).toHaveLength(1);
  });
});
