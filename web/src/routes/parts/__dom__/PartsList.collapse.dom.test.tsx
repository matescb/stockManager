/**
 * DOM tests for the collapsible category rail, and for the width it buys
 * back.
 *
 * Two things are being pinned:
 *
 *  1. **The rail collapses, stays escapable, and is remembered.** A
 *     collapsed rail hides the tree but keeps a named re-open control; a
 *     corrupt stored value falls back to expanded rather than stranding
 *     the user in a state they'd need devtools to leave.
 *  2. **The preview pane's breakpoint follows the rail.** With the rail
 *     expanded the pane still waits for `xl` — the layout #907 shipped.
 *     Collapse the rail and it comes forward to `lg`, because the 240px
 *     the rail was costing is what made `lg` unusable. Below `lg` neither
 *     rail state changes anything: a row click navigates, exactly as it
 *     did before either feature existed.
 *
 * Viewport width is driven through a `matchMedia` stub that answers any
 * `min-width` query honestly, and the widths under test are read out of
 * `LG_VIEWPORT_QUERY` / `XL_VIEWPORT_QUERY` — so moving a breakpoint moves
 * these tests with it instead of leaving them passing against a stale
 * hard-coded number.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Part } from "@/lib/schemas";
import { LG_VIEWPORT_QUERY, XL_VIEWPORT_QUERY } from "@/lib/useMediaQuery";
import { panelCollapseStorageKey } from "@/lib/usePanelCollapse";

vi.mock("@/instrument", () => ({}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

vi.mock("@/lib/queryKeys", () => ({
  useWsKey: (...args: unknown[]) => ["ws-1", ...args],
  wsKeyOf: (...args: unknown[]) => ["ws-1", ...args],
  archivePartKeys: () => [],
}));

vi.mock("@/components/ConfirmDialog", () => ({
  useConfirm: () => vi.fn(async () => false),
}));

const PART_ONE_ID = "11111111-1111-4111-8111-111111111111";
const PART_TWO_ID = "22222222-2222-4222-8222-222222222222";
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

const ROW_ONE = makePart({ id: PART_ONE_ID, name: "Resistor 10k", on_hand: 1200 });
const ROW_TWO = makePart({ id: PART_TWO_ID, name: "Capacitor 100n", on_hand: 42 });

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
    if (url === "/storage") return Promise.resolve([]);
    if (/^\/parts\/[^/]+\/stock$/.test(url)) {
      return Promise.resolve({ total_on_hand: 0, rows: [] });
    }
    const detail = /^\/parts\/([^/?]+)$/.exec(url);
    if (detail) {
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

/** The pixel threshold inside a `(min-width: Npx)` query. */
function widthOf(query: string): number {
  const match = /\(min-width:\s*(\d+)px\)/.exec(query);
  if (!match) throw new Error(`not a min-width query: ${query}`);
  return Number(match[1]);
}

const LG_WIDTH = widthOf(LG_VIEWPORT_QUERY);
const XL_WIDTH = widthOf(XL_VIEWPORT_QUERY);

/** Answers every `min-width` query against one viewport width. */
function setViewport(width: number) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: width >= widthOf(query),
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

const RAIL_KEY = panelCollapseStorageKey("parts-categories", "ws-1");

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("workspaceId", "ws-1");
  setViewport(XL_WIDTH);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("parts category rail collapse", () => {
  it("starts expanded, with a toggle that says what it will do", async () => {
    renderList();

    const toggle = await screen.findByRole("button", { name: "Hide categories" });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.getAttribute("aria-controls")).toBe("parts-category-tree");
    expect(await screen.findByRole("treeitem", { name: /Passives/ })).toBeTruthy();
  });

  it("collapsing hides the tree but keeps a named way back", async () => {
    const user = userEvent.setup();
    renderList();

    await user.click(await screen.findByRole("button", { name: "Hide categories" }));

    const reopen = screen.getByRole("button", { name: "Show categories" });
    expect(reopen.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("treeitem", { name: /Passives/ })).toBeNull();

    await user.click(reopen);
    expect(await screen.findByRole("treeitem", { name: /Passives/ })).toBeTruthy();
  });

  it("names the active filter on the collapsed strip", async () => {
    const user = userEvent.setup();
    renderList(`/parts?category=${CATEGORY_ID}`);

    await user.click(await screen.findByRole("button", { name: "Hide categories" }));

    // The tree is gone, so the toggle is the only thing left that can say
    // why the list is short.
    expect(
      screen.getByRole("button", { name: "Show categories (filtered by Passives)" }),
    ).toBeTruthy();
  });

  it("the collapsed state is remembered across a remount", async () => {
    const user = userEvent.setup();
    const first = renderList();

    await user.click(await screen.findByRole("button", { name: "Hide categories" }));
    first.unmount();

    renderList();
    expect(await screen.findByRole("button", { name: "Show categories" })).toBeTruthy();
  });

  it("a corrupt stored value falls back to expanded", async () => {
    localStorage.setItem(RAIL_KEY, "{not json");

    renderList();

    expect(await screen.findByRole("button", { name: "Hide categories" })).toBeTruthy();
    expect(await screen.findByRole("treeitem", { name: /Passives/ })).toBeTruthy();
  });

  it("collapsing the rail does not disturb the category filter", async () => {
    const user = userEvent.setup();
    renderList(`/parts?category=${CATEGORY_ID}`);

    await user.click(await screen.findByRole("button", { name: "Hide categories" }));

    expect(locationText()).toContain(`category=${CATEGORY_ID}`);
  });
});

describe("preview breakpoint follows the rail", () => {
  it("at lg with the rail expanded, a row click still navigates", async () => {
    setViewport(LG_WIDTH);
    const user = userEvent.setup();
    renderList();

    await user.click(await rowFor("Resistor 10k"));

    await waitFor(() => expect(screen.getByText("FULL PART PAGE")).toBeTruthy());
    expect(locationText()).toBe(`/parts/${PART_ONE_ID}/info`);
  });

  it("at lg with the rail collapsed, a row click opens the pane", async () => {
    localStorage.setItem(RAIL_KEY, JSON.stringify({ collapsed: true }));
    setViewport(LG_WIDTH);
    const user = userEvent.setup();
    renderList();

    await user.click(await rowFor("Resistor 10k"));

    await waitFor(() => expect(locationText()).toBe(`/parts?sel=${PART_ONE_ID}`));
    expect(screen.queryByText("FULL PART PAGE")).toBeNull();
    expect(screen.getByTestId("part-preview-pane")).toBeTruthy();
  });

  it("collapsing the rail at lg reveals a pane the deep link already asked for", async () => {
    setViewport(LG_WIDTH);
    const user = userEvent.setup();
    renderList(`/parts?sel=${PART_ONE_ID}`);

    // `?sel=` is honoured but inert while the rail owns its column.
    await rowFor("Resistor 10k");
    expect(screen.queryByTestId("part-preview-pane")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Hide categories" }));

    expect(await screen.findByTestId("part-preview-pane")).toBeTruthy();
    expect(locationText()).toBe(`/parts?sel=${PART_ONE_ID}`);
  });

  it("the pane's visibility class matches the breakpoint it was allowed at", async () => {
    localStorage.setItem(RAIL_KEY, JSON.stringify({ collapsed: true }));
    setViewport(LG_WIDTH);
    renderList(`/parts?sel=${PART_ONE_ID}`);

    // The CSS that reveals the pane has to agree with the hook that filled
    // it, or a resize leaves a selected row pointing at an invisible pane.
    const pane = await screen.findByTestId("part-preview-pane");
    expect(pane.className).toContain("lg:flex");
    expect(pane.className).not.toContain("xl:flex");
  });

  it("at xl the pane still waits for xl when the rail is expanded", async () => {
    setViewport(XL_WIDTH);
    renderList(`/parts?sel=${PART_ONE_ID}`);

    const pane = await screen.findByTestId("part-preview-pane");
    expect(pane.className).toContain("xl:flex");
    expect(pane.className).not.toContain("lg:flex");
  });

  it.each([
    ["expanded", false],
    ["collapsed", true],
  ])("below lg with the rail %s, a row click navigates", async (_label, collapsed) => {
    if (collapsed) localStorage.setItem(RAIL_KEY, JSON.stringify({ collapsed }));
    setViewport(LG_WIDTH - 1);
    const user = userEvent.setup();
    renderList();

    await user.click(await rowFor("Capacitor 100n"));

    await waitFor(() => expect(screen.getByText("FULL PART PAGE")).toBeTruthy());
    expect(locationText()).toBe(`/parts/${PART_TWO_ID}/info`);
    expect(screen.queryByTestId("part-preview-pane")).toBeNull();
  });

  it("below lg a ?sel= deep link renders no pane, either way", async () => {
    localStorage.setItem(RAIL_KEY, JSON.stringify({ collapsed: true }));
    setViewport(LG_WIDTH - 1);
    renderList(`/parts?sel=${PART_TWO_ID}`);

    await rowFor("Capacitor 100n");
    expect(screen.queryByTestId("part-preview-pane")).toBeNull();
    expect(locationText()).toBe(`/parts?sel=${PART_TWO_ID}`);
  });
});
