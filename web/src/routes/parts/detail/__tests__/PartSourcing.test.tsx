// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { api } from "@/lib/api";
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

function renderPartSourcing() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/parts/11111111-1111-4111-8111-111111111111/sourcing"]}>
        <Routes>
          <Route path="/parts/:partId" element={<Outlet context={{ part }} />}>
            <Route path="sourcing" element={<PartSourcing />} />
          </Route>
        </Routes>
      </MemoryRouter>
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
});
