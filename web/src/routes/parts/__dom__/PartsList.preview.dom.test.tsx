/**
 * DOM tests for the parts-list preview pane.
 *
 * The four behaviours worth pinning, because each one is a promise the
 * feature makes and none is visible in a type signature:
 *
 *  1. At `xl` and up, a row click SELECTS (`?sel=<id>`) instead of
 *     navigating. The whole point of the feature.
 *  2. The pane paints from the list row the click already had, BEFORE
 *     `GET /parts/:id` resolves. A preview that spins on every selection
 *     is worse than the navigation it replaced, so this is asserted
 *     against a deliberately un-resolved fetch.
 *  3. `?sel=<id>` in the URL opens the pane on first render — the reason
 *     selection lives in the URL rather than in component state.
 *  4. **Below `xl`, a row click still navigates.** Narrow viewports must
 *     be byte-for-byte what they were before this feature existed.
 *
 * Plus the two keyboard paths: Arrow Up/Down drives the pane, and Escape
 * closes it.
 *
 * Viewport width is controlled by stubbing `window.matchMedia` — jsdom's
 * own implementation reports `matches: false` for everything, which is
 * exactly the "narrow" case, so test 4 gets the honest default.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Part } from "@/lib/schemas";
import { XL_VIEWPORT_QUERY } from "@/lib/useMediaQuery";

// ---------------------------------------------------------------------------
// Stubs. `api` is the only HTTP entry point per CLAUDE.md, so stubbing it
// covers every request the tree makes.
// ---------------------------------------------------------------------------
vi.mock("@/instrument", () => ({}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

vi.mock("@/lib/queryKeys", () => ({
  useWsKey: (...args: unknown[]) => ["ws-1", ...args],
  wsKeyOf: (...args: unknown[]) => ["ws-1", ...args],
  archivePartKeys: () => [],
}));

// `useConfirm` throws outside its provider and the preview never calls it.
vi.mock("@/components/ConfirmDialog", () => ({
  useConfirm: () => vi.fn(async () => false),
}));

// `PartSchema.id` is `z.string().uuid()` and `PagedPartsSchema` is parsed
// at the boundary, so fixtures need real UUIDs or the list refuses to load.
const PART_ONE_ID = "11111111-1111-4111-8111-111111111111";
const PART_TWO_ID = "22222222-2222-4222-8222-222222222222";
const STORAGE_ID = "33333333-3333-4333-8333-333333333333";
const CATEGORY_ID = "44444444-4444-4444-8444-444444444444";

/** One root category, so the rail from #909 has something to click. */
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

/** Set by individual tests to hold `GET /parts/:id` open indefinitely. */
const partDetailBehavior: { mode: "resolve" | "pending" } = { mode: "resolve" };

function makePart(over: Partial<Part> & { id: string; name: string }): Part {
  return {
    part_type: "local",
    manufacturer: null,
    mpn: null,
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
    linked_provider: null,
    linked_external_id: null,
    last_refresh_at: null,
    description_locally_edited: false,
    archived_at: null,
    on_hand: 0,
    reserved: 0,
    available: 0,
    image_url: null,
    ...over,
  } as Part;
}

const ROW_ONE = makePart({
  id: PART_ONE_ID,
  name: "Resistor 10k",
  mpn: "RC0805FR-0710KL",
  manufacturer: "Yageo",
  on_hand: 1200,
});
const ROW_TWO = makePart({
  id: PART_TWO_ID,
  name: "Capacitor 100n",
  mpn: "CC0805KRX7R9BB104",
  manufacturer: "Yageo",
  on_hand: 42,
});

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
  const get = vi.fn((url: string) => {
    if (url === "/storage") return Promise.resolve([{ id: STORAGE_ID, name: "Shelf A" }]);
    if (/^\/parts\/[^/]+\/stock$/.test(url)) {
      return Promise.resolve({
        total_on_hand: 1200,
        rows: [{ storage_location_id: STORAGE_ID, lot_id: null, quantity: 1200 }],
      });
    }
    const detail = /^\/parts\/([^/?]+)$/.exec(url);
    if (detail) {
      if (partDetailBehavior.mode === "pending") return new Promise(() => {});
      // Must echo the requested part — the pane hydrates over the row it
      // was handed, so a mock that always returns the same part would hide
      // a bug where the wrong id is fetched.
      return Promise.resolve([ROW_ONE, ROW_TWO].find((p) => p.id === detail[1]) ?? ROW_ONE);
    }
    return Promise.resolve([]);
  });
  return {
    ApiError,
    getPaged: vi.fn(() => Promise.resolve({ items: [ROW_ONE, ROW_TWO], next_cursor: null })),
    api: {
      get,
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

// ---------------------------------------------------------------------------
// Viewport control
// ---------------------------------------------------------------------------
function setViewport(isWide: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      // Keyed off the exported constant, so moving the breakpoint moves
      // these tests with it instead of silently passing against a stale
      // hard-coded width.
      matches: isWide && query === XL_VIEWPORT_QUERY,
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

/** Renders the current location so navigation away from /parts is visible. */
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search}</div>;
}

function renderList(initialEntry = "/parts") {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <LocationProbe />
        <Routes>
          <Route path="/parts" element={<PartsList />} />
          <Route path="/parts/:partId/info" element={<div>FULL PART PAGE</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * The table `<tr>` containing `text`. Scoped to the table because the
 * pane repeats the part name, and `DataTable`'s own row `aria-label`
 * derives from the first accessor column (part type), so it can't tell
 * two rows apart here.
 */
async function rowFor(text: string): Promise<HTMLElement> {
  const table = await screen.findByRole("table");
  const cell = await within(table).findByText(text);
  const row = cell.closest("tr");
  if (!row) throw new Error(`no <tr> around "${text}"`);
  return row as HTMLElement;
}

function locationText(): string {
  return screen.getByTestId("location").textContent ?? "";
}

beforeEach(() => {
  partDetailBehavior.mode = "resolve";
  setViewport(true);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("parts list preview pane", () => {
  it("selecting a row sets ?sel= instead of navigating away", async () => {
    const user = userEvent.setup();
    renderList();

    await user.click(await rowFor("Resistor 10k"));

    await waitFor(() => expect(locationText()).toBe(`/parts?sel=${PART_ONE_ID}`));
    // Still on the list — the full part page never mounted.
    expect(screen.queryByText("FULL PART PAGE")).toBeNull();
    expect(screen.getByTestId("part-preview-pane")).toBeTruthy();
  });

  it("paints the preview from the list row before the part fetch resolves", async () => {
    // `GET /parts/:id` never settles, so anything on screen came from the
    // row the click already had.
    partDetailBehavior.mode = "pending";
    const user = userEvent.setup();
    renderList();

    await user.click(await rowFor("Capacitor 100n"));

    const pane = await screen.findByTestId("part-preview-pane");
    expect(within(pane).getByRole("heading", { name: "Capacitor 100n" })).toBeTruthy();
    expect(pane.textContent).toContain("CC0805KRX7R9BB104");
    expect(pane.textContent).toContain("Yageo");
    // On hand comes through `formatQuantity`, never an integer coercion.
    expect(within(pane).getByText("42")).toBeTruthy();
  });

  it("deep-linking to ?sel=<id> opens the preview directly", async () => {
    renderList(`/parts?sel=${PART_TWO_ID}`);

    const pane = await screen.findByTestId("part-preview-pane");
    expect(within(pane).getByRole("heading", { name: "Capacitor 100n" })).toBeTruthy();
    expect(screen.queryByText("FULL PART PAGE")).toBeNull();
  });

  it("below xl, a row click navigates to the full part page as before", async () => {
    setViewport(false);
    const user = userEvent.setup();
    renderList();

    await user.click(await rowFor("Resistor 10k"));

    await waitFor(() => expect(screen.getByText("FULL PART PAGE")).toBeTruthy());
    expect(locationText()).toBe(`/parts/${PART_ONE_ID}/info`);
    expect(screen.queryByTestId("part-preview-pane")).toBeNull();
  });

  it("below xl, a ?sel= deep link renders no pane and hijacks nothing", async () => {
    setViewport(false);
    renderList(`/parts?sel=${PART_TWO_ID}`);

    await rowFor("Capacitor 100n");
    expect(screen.queryByTestId("part-preview-pane")).toBeNull();
    expect(locationText()).toBe(`/parts?sel=${PART_TWO_ID}`);
  });

  it("arrow-key row navigation drives the preview", async () => {
    const user = userEvent.setup();
    renderList();

    const first = await rowFor("Resistor 10k");
    first.focus();
    await user.keyboard("{ArrowDown}");

    const pane = await screen.findByTestId("part-preview-pane");
    expect(within(pane).getByRole("heading", { name: "Capacitor 100n" })).toBeTruthy();
    await waitFor(() => expect(locationText()).toBe(`/parts?sel=${PART_TWO_ID}`));
  });

  it("Escape closes the preview without leaving the list", async () => {
    const user = userEvent.setup();
    renderList(`/parts?sel=${PART_ONE_ID}`);

    await screen.findByTestId("part-preview-pane");
    (await rowFor("Resistor 10k")).focus();
    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByTestId("part-preview-pane")).toBeNull());
    expect(locationText()).toBe("/parts");
    expect(screen.queryByText("FULL PART PAGE")).toBeNull();
  });

  it("the close button clears the selection", async () => {
    const user = userEvent.setup();
    renderList(`/parts?sel=${PART_ONE_ID}`);

    const pane = await screen.findByTestId("part-preview-pane");
    await user.click(within(pane).getByRole("button", { name: "Close preview" }));

    await waitFor(() => expect(screen.queryByTestId("part-preview-pane")).toBeNull());
  });

  it("the pane is a labelled landmark, not a modal dialog", async () => {
    renderList(`/parts?sel=${PART_ONE_ID}`);

    const pane = await screen.findByTestId("part-preview-pane");
    expect(pane.tagName).toBe("ASIDE");
    expect(pane.getAttribute("aria-label")).toMatch(/Resistor 10k/);
    // No focus trap, no aria-modal — the list stays browsable.
    expect(pane.getAttribute("aria-modal")).toBeNull();
    expect(pane.getAttribute("role")).toBeNull();
  });

  it("shows where the part is stocked", async () => {
    renderList(`/parts?sel=${PART_ONE_ID}`);

    const pane = await screen.findByTestId("part-preview-pane");
    await waitFor(() => expect(within(pane).getByText("Shelf A")).toBeTruthy());
    expect(within(pane).getByText("Where it is")).toBeTruthy();
  });

  // -------------------------------------------------------------------
  // Composition with the category filter (#909). Both predicates now live
  // in the search params, and each writer must preserve the other's key —
  // a `setSearchParams({...})` object literal on either side would silently
  // drop the other.
  // -------------------------------------------------------------------

  it("a category filter and a selection coexist in the URL", async () => {
    renderList(`/parts?category=${CATEGORY_ID}&sel=${PART_ONE_ID}`);

    const pane = await screen.findByTestId("part-preview-pane");
    expect(within(pane).getByRole("heading", { name: "Resistor 10k" })).toBeTruthy();
    // The category predicate survived alongside the selection.
    expect(locationText()).toContain(`category=${CATEGORY_ID}`);
    expect(locationText()).toContain(`sel=${PART_ONE_ID}`);
  });

  it("selecting a row keeps an active category filter", async () => {
    const user = userEvent.setup();
    renderList(`/parts?category=${CATEGORY_ID}`);

    await user.click(await rowFor("Resistor 10k"));

    await waitFor(() => expect(locationText()).toContain(`sel=${PART_ONE_ID}`));
    expect(locationText()).toContain(`category=${CATEGORY_ID}`);
  });

  it("changing the category keeps the preview selection", async () => {
    const user = userEvent.setup();
    renderList(`/parts?sel=${PART_ONE_ID}`);

    await screen.findByTestId("part-preview-pane");
    await user.click(await screen.findByRole("treeitem", { name: /Passives/ }));

    await waitFor(() => expect(locationText()).toContain(`category=${CATEGORY_ID}`));
    // …and the pane is still open on the same part.
    expect(locationText()).toContain(`sel=${PART_ONE_ID}`);
    expect(screen.getByTestId("part-preview-pane")).toBeTruthy();
  });

  it("clearing the category keeps the preview selection", async () => {
    const user = userEvent.setup();
    renderList(`/parts?category=${CATEGORY_ID}&sel=${PART_ONE_ID}`);

    await screen.findByTestId("part-preview-pane");
    await user.click(await screen.findByRole("treeitem", { name: /Passives/ }));

    await waitFor(() => expect(locationText()).not.toContain("category="));
    expect(locationText()).toContain(`sel=${PART_ONE_ID}`);
  });

  it("offers a link to the full page rather than replacing it", async () => {
    renderList(`/parts?sel=${PART_ONE_ID}`);

    const pane = await screen.findByTestId("part-preview-pane");
    const link = within(pane).getByRole("link", { name: /Open full page/ });
    expect(link.getAttribute("href")).toBe(`/parts/${PART_ONE_ID}/info`);
  });
});
