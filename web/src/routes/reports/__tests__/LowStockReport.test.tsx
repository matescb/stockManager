// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { Order } from "@/types";
import { LowStockReport } from "../Reports";

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

const lowStockRow = {
  part_id: "part-1",
  name: "STM32",
  manufacturer: "ST",
  mpn: "STM32F103C8T6",
  on_hand: 4,
  reserved: 0,
  available: 4,
  threshold: 20,
  short_by: 16,
};

const sourcedLowStock = {
  rows: [
    {
      ...lowStockRow,
      sourcing: {
        authorized_stock: 120,
        offers: [
          {
            mpn: "STM32F103C8T6",
            distributor: "DigiKey",
            stock: 120,
            unit_price: "1.25",
            currency: "USD",
            packaging: "Tape",
            moq: 25,
            lead_time_days: 3,
            url: "https://www.trustedparts.com/digikey/stm32",
          },
        ],
        best_offer: {
          mpn: "STM32F103C8T6",
          distributor: "DigiKey",
          stock: 120,
          unit_price: "1.25",
          currency: "USD",
          packaging: "Tape",
          moq: 25,
          lead_time_days: 3,
          url: "https://www.trustedparts.com/digikey/stm32",
        },
        est_replenishment_cost: "31.25",
        lead_time_days: 3,
        preferred_distributor_available: true,
        cache_hit: false,
        fetched_at: "2026-05-08T12:00:00+00:00",
      },
    },
  ],
  sourcing_status: "ok" as const,
  powered_by: "TrustedParts" as const,
  links: {
    primary: "https://www.trustedparts.com/",
    attribution: "https://www.trustedparts.com/en/about",
  },
};

const draftOrder: Order = {
  id: "order-1",
  name: "May sourcing",
  order_type: "purchase",
  supplier: "DigiKey",
  status: "draft",
  ordered_on: null,
  expected_on: null,
  received_on: null,
  currency: "USD",
  comments: null,
  archived_at: null,
  totals: { ordered: 0, received: 0 },
  created_at: "2026-05-08T12:00:00+00:00",
  updated_at: "2026-05-08T12:00:00+00:00",
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

function renderReport(initialPath = "/reports") {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/reports" element={<><LocationProbe /><LowStockReport /></>} />
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

describe("LowStockReport", () => {
  it("toggling include sourcing reflects in URL", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockImplementation(async path => {
      if (path === "/reports/low-stock") return [lowStockRow] as never;
      if (path === "/reports/low-stock?include_sourcing=true") return sourcedLowStock as never;
      throw new Error(`unexpected GET ${path}`);
    });

    renderReport();

    expect(await screen.findByText("STM32")).toBeDefined();
    await user.click(screen.getByLabelText("Include sourcing data"));

    await waitFor(() => {
      expect(screen.getByTestId("location").textContent).toBe("/reports?include_sourcing=true");
    });
    expect(await screen.findAllByText("Authorized stock")).toHaveLength(2);
    expect(screen.getByText("Powered by TrustedParts")).toBeDefined();
  });

  it("not-configured banner renders when status flag set", async () => {
    vi.spyOn(api, "get").mockResolvedValue({
      rows: [{ ...lowStockRow, sourcing: null }],
      sourcing_status: "not_configured",
      powered_by: null,
      links: null,
    });

    renderReport("/reports?include_sourcing=true");

    expect(await screen.findByText("Sourcing not configured.")).toBeDefined();
    expect(screen.getByLabelText("Include sourcing data")).toHaveProperty("checked", true);
  });

  it("Create draft PO opens modal with prefilled source", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockImplementation(async path => {
      if (path === "/reports/low-stock?include_sourcing=true") return sourcedLowStock as never;
      if (path === "/orders?order_status=draft") return [draftOrder] as never;
      throw new Error(`unexpected GET ${path}`);
    });

    renderReport("/reports?include_sourcing=true");

    await user.click(await screen.findByRole("button", { name: "Create draft PO" }));

    expect(await screen.findByRole("dialog", { name: "Create order line" })).toBeDefined();
    await waitFor(() => {
      expect((screen.getByLabelText("Order") as HTMLSelectElement).value).toBe("order-1");
    });
    expect(screen.getByDisplayValue("25")).toBeDefined();
    expect(screen.getByDisplayValue("1.25")).toBeDefined();
    expect(screen.getByDisplayValue("USD")).toBeDefined();
    expect(screen.getByText("DigiKey")).toBeDefined();
  });
});
