// @vitest-environment jsdom
/**
 * The category filter's URL contract on `/parts`.
 *
 * Two things are pinned here and nowhere else: the filter is *deep-linkable*
 * (it round-trips through the query string, so a link to a filtered list
 * survives a reload, a share, and the login redirect), and it reaches the
 * server as request parameters rather than being applied to the rows that
 * come back — the backend applies it before `paginate()`, and a client-side
 * filter would silently undo that.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as apiModule from "@/lib/api";
import { ConfirmDialogProvider } from "@/components/ConfirmDialog";
import PartsList from "../PartsList";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ workspaceId: "ws-1" }) }));
// The batch-print dialog pulls in the label pipeline; irrelevant here.
vi.mock("@/routes/labels/BatchPrintDialog", () => ({ default: () => null }));

const CATEGORIES = [
  {
    id: "aaaaaaaa-1111-4111-8111-111111111111",
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
  {
    id: "bbbbbbbb-2222-4222-8222-222222222222",
    name: "Resistors",
    description: null,
    sort_order: 0,
    refdes_prefix: null,
    default_symbol_ref: null,
    default_footprint_ref: null,
    footprint_filters: null,
    library_slug: "resistors",
    parent_id: "aaaaaaaa-1111-4111-8111-111111111111",
    archived_at: null,
  },
];

let requestedUrls: string[] = [];
let currentSearch = "";

function LocationProbe() {
  currentSearch = useLocation().search;
  return null;
}

function renderList(initialUrl = "/parts") {
  requestedUrls = [];
  vi.spyOn(apiModule.api.parsed, "get").mockResolvedValue(CATEGORIES);
  vi.spyOn(apiModule, "getPaged").mockImplementation(async (url: string) => {
    requestedUrls.push(url);
    return { items: [], next_cursor: null };
  });

  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ConfirmDialogProvider>
        <MemoryRouter initialEntries={[initialUrl]}>
          <LocationProbe />
          <Routes>
            <Route path="/parts" element={<PartsList />} />
          </Routes>
        </MemoryRouter>
      </ConfirmDialogProvider>
    </QueryClientProvider>,
  );
}

const lastUrl = () => requestedUrls[requestedUrls.length - 1];

beforeEach(() => {
  cleanup();
  localStorage.clear();
  currentSearch = "";
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("PartsList — category filter", () => {
  it("sends no category parameter when nothing is selected", async () => {
    renderList();
    await waitFor(() => expect(requestedUrls.length).toBeGreaterThan(0));
    expect(lastUrl()).not.toContain("category_id");
  });

  it("puts the selection in the URL and sends it to the API", async () => {
    renderList();
    await screen.findByRole("treeitem", { name: /Passives/ });

    fireEvent.click(screen.getByRole("treeitem", { name: /Passives/ }));

    await waitFor(() =>
      expect(lastUrl()).toContain(`category_id=${CATEGORIES[0].id}`),
    );
    expect(currentSearch).toContain(`category=${CATEGORIES[0].id}`);
    // Descendants are the default; the flag is only sent to turn them off.
    expect(lastUrl()).not.toContain("include_descendants");
  });

  it("restores the filter from a deep link, without a click", async () => {
    renderList(`/parts?category=${CATEGORIES[1].id}`);
    await waitFor(() =>
      expect(lastUrl()).toContain(`category_id=${CATEGORIES[1].id}`),
    );
    // ...and the tree opens far enough to show which node that is.
    const node = await screen.findByRole("treeitem", { name: /Resistors/ });
    expect(node.getAttribute("aria-selected")).toBe("true");
  });

  it("honours ?exact=1 as include_descendants=false", async () => {
    renderList(`/parts?category=${CATEGORIES[0].id}&exact=1`);
    await waitFor(() => expect(lastUrl()).toContain("include_descendants=false"));
  });

  it("toggling subcategories off rewrites the URL and refetches", async () => {
    renderList(`/parts?category=${CATEGORIES[0].id}`);
    await waitFor(() => expect(lastUrl()).toContain("category_id"));

    // The rail and the narrow-screen bar both render a toggle; which one
    // the user sees is a CSS media query, which jsdom does not apply. They
    // are wired to the same handler, so either will do.
    const toggles = await screen.findAllByLabelText("Include subcategories");
    fireEvent.click(toggles[0]);

    await waitFor(() => expect(currentSearch).toContain("exact=1"));
    await waitFor(() => expect(lastUrl()).toContain("include_descendants=false"));
  });

  it("clearing the filter drops both params from the URL", async () => {
    renderList(`/parts?category=${CATEGORIES[0].id}&exact=1`);
    await waitFor(() => expect(lastUrl()).toContain("category_id"));

    fireEvent.click(screen.getByRole("button", { name: "All parts" }));

    await waitFor(() => expect(currentSearch).not.toContain("category="));
    expect(currentSearch).not.toContain("exact=");
    await waitFor(() => expect(lastUrl()).not.toContain("category_id"));
  });

  it("keeps the paged shape — the filter never becomes a client-side one", async () => {
    renderList(`/parts?category=${CATEGORIES[0].id}`);
    await waitFor(() => expect(lastUrl()).toContain("category_id"));
    expect(lastUrl()).toContain("paged=true");
    expect(lastUrl()).toContain("limit=");
  });
});
