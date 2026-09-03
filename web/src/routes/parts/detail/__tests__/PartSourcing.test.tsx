// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { api } from "@/lib/api";
import { ConfirmDialogProvider } from "@/components/ConfirmDialog";
import type { CustomFieldRow, Part } from "@/types";
import PartSourcing from "../PartSourcing";

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

const part: Part = {
  id: "11111111-1111-4111-8111-111111111111",
  part_type: "linked",
  name: "STM32",
  manufacturer: "STMicroelectronics",
  mpn: "STM32F103C8T6",
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
  linked_provider: "digikey",
  linked_external_id: "dk-1",
  last_refresh_at: "2026-05-08T12:00:00+00:00",
  description_locally_edited: false,
  archived_at: null,
  on_hand: 0,
  reserved: 0,
  available: 0,
  image_url: null,
};

const fields: CustomFieldRow[] = [
  {
    id: "22222222-2222-4222-8222-222222222222",
    key: "In stock (qty)",
    value: "42",
    source: "provider",
    original_value: null,
  },
  {
    id: "33333333-3333-4333-8333-333333333333",
    key: "Unit price (10+)",
    value: "1.23 USD",
    source: "provider",
    original_value: null,
  },
  {
    id: "44444444-4444-4444-8444-444444444444",
    key: "Resistance",
    value: "10k",
    source: "provider",
    original_value: null,
  },
];

/** The same part, additionally linked to Mouser as a SECONDARY provider. */
const partWithMouserLink: Part = {
  ...part,
  provider_links: [
    {
      provider: "digikey",
      external_id: "dk-1",
      source_url: "https://www.digikey.com/p/1",
      last_refresh_at: "2026-05-08T12:00:00+00:00",
    },
    {
      provider: "mouser",
      external_id: "mouser-ext-1",
      source_url: "https://www.mouser.com/p/1",
      last_refresh_at: "2026-05-09T12:00:00+00:00",
    },
  ],
};

const mouserFields: CustomFieldRow[] = [
  {
    id: "55555555-5555-4555-8555-555555555555",
    key: "mouser:Lead time",
    value: "8 weeks",
    source: "provider",
    original_value: null,
  },
  {
    id: "66666666-6666-4666-8666-666666666666",
    key: "mouser:In stock (qty)",
    value: "7",
    source: "provider",
    original_value: null,
  },
];

function renderPartSourcing(withPart: Part = part) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ConfirmDialogProvider>
        <MemoryRouter initialEntries={["/parts/11111111-1111-4111-8111-111111111111/sourcing"]}>
          <Routes>
            <Route path="/parts/:partId" element={<Outlet context={{ part: withPart }} />}>
              <Route path="sourcing" element={<PartSourcing />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ConfirmDialogProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("PartSourcing", () => {
  it("renders catalog fields in the shared DataTable", async () => {
    vi.spyOn(api, "get").mockResolvedValue(fields);

    renderPartSourcing();

    const table = await screen.findByRole("table");
    expect(within(table).getByText("In stock (qty)")).toBeDefined();
    expect(within(table).getByText("42")).toBeDefined();
    expect(within(table).getByText("Unit price (10+)")).toBeDefined();
    expect(within(table).queryByText("Resistance")).toBeNull();
    expect(screen.getByPlaceholderText("Search catalog data...")).toBeDefined();
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeDefined();
  });

  it("gives each secondary provider link its own section", async () => {
    vi.spyOn(api, "get").mockResolvedValue([...fields, ...mouserFields]);

    renderPartSourcing(partWithMouserLink);

    // The Mouser section renders its own heading, product link and the
    // `mouser:`-prefixed rows — with the prefix stripped for display.
    expect(await screen.findByText("Additional provider")).toBeDefined();
    const mouserTable = await screen.findByPlaceholderText("Search Mouser data...");
    expect(mouserTable).toBeDefined();
    expect(screen.getByText("mouser-ext-1")).toBeDefined();
    expect(
      screen.getByRole("link", { name: "Open at Mouser" }).getAttribute("href"),
    ).toBe("https://www.mouser.com/p/1");
    expect(screen.getByText("Lead time")).toBeDefined();
    // The namespaced key never leaks into the UI verbatim.
    expect(screen.queryByText("mouser:Lead time")).toBeNull();
  });

  it("keeps the primary table free of namespaced rows", async () => {
    vi.spyOn(api, "get").mockResolvedValue([...fields, ...mouserFields]);

    renderPartSourcing(partWithMouserLink);

    const primary = await screen.findByPlaceholderText("Search catalog data...");
    const primaryTable = primary.closest(".card")?.querySelector("table");
    expect(primaryTable).toBeTruthy();
    expect(within(primaryTable as HTMLElement).getByText("In stock (qty)")).toBeDefined();
    expect(within(primaryTable as HTMLElement).queryByText("Lead time")).toBeNull();
  });

  it("refreshes a secondary provider through ?provider=", async () => {
    vi.spyOn(api, "get").mockResolvedValue([...fields, ...mouserFields]);
    const post = vi.spyOn(api, "post").mockResolvedValue({});
    const user = userEvent.setup();

    renderPartSourcing(partWithMouserLink);

    await user.click(await screen.findByRole("button", { name: "Refresh Mouser" }));

    expect(post).toHaveBeenCalledWith(
      `/parts/${part.id}/refresh-from-provider?provider=mouser`,
    );
  });

  it("unlinks a secondary provider only after confirmation", async () => {
    vi.spyOn(api, "get").mockResolvedValue([...fields, ...mouserFields]);
    const del = vi.spyOn(api, "delete").mockResolvedValue(null);
    const user = userEvent.setup();

    renderPartSourcing(partWithMouserLink);

    // The card's button only opens the confirm dialog...
    await user.click(await screen.findByRole("button", { name: "Unlink Mouser" }));
    expect(del).not.toHaveBeenCalled();

    // ...the dialog's own button is what actually calls the endpoint.
    await user.click(await screen.findByRole("button", { name: "Unlink" }));
    expect(del).toHaveBeenCalledWith(`/parts/${part.id}/provider-links/mouser`);
  });
});
