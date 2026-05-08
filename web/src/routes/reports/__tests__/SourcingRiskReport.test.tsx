// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import SourcingRiskReport from "../SourcingRiskReport";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

function report(rows: unknown[]) {
  return {
    rows,
    sourcing_status: { state: "ok", message: "OK" },
    powered_by: "TrustedParts",
    fetched_at: "2026-05-08T12:00:00+00:00",
    partial: false,
    cache_hit: false,
    links: {
      primary: "https://www.trustedparts.com/",
      attribution: "https://www.trustedparts.com/en/about",
    },
  };
}

function row(overrides: Record<string, unknown>) {
  return {
    part_id: "part-1",
    name: "STM32",
    manufacturer: null,
    mpn: "STM32F103",
    on_hand: 4,
    distributors_with_stock: ["DigiKey"],
    authorized_stock: 20,
    best_offer: {
      mpn: "STM32F103",
      distributor: "DigiKey",
      sku: "DK-1",
      stock: 20,
      unit_price: "1.25",
      currency: "USD",
      packaging: null,
      moq: 1,
      lead_time_days: 3,
      url: "https://www.trustedparts.com/digikey/stm32",
    },
    lead_time_days: 3,
    typical_reorder_quantity: 10,
    historical_unit_cost: null,
    historical_currency: null,
    price_delta_pct: null,
    risk_flags: ["single_source"],
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SourcingRiskReport />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("SourcingRiskReport", () => {
  it("renders flag pills", async () => {
    vi.spyOn(api, "get").mockResolvedValue(report([
      row({ risk_flags: ["single_source", "lead_time_long", "price_delta"] }),
    ]));

    renderPage();

    expect(await screen.findByText("Single source")).toBeDefined();
    expect(screen.getByText("Long lead time")).toBeDefined();
    expect(screen.getByText("Price delta")).toBeDefined();
    expect(screen.getByLabelText("Source: TrustedParts")).toBeDefined();
    expect(screen.getByRole("link", { name: "Powered by TrustedParts" })).toBeDefined();
  });

  it("show only flagged toggle filters list through the report query", async () => {
    const get = vi.spyOn(api, "get").mockImplementation(async path => {
      if (String(path).includes("only_with_flags=true")) {
        return report([row({ part_id: "part-risk", name: "Risky", mpn: "RISKY" })]) as never;
      }
      return report([
        row({ part_id: "part-risk", name: "Risky", mpn: "RISKY" }),
        row({ part_id: "part-clean", name: "Clean", mpn: "CLEAN", distributors_with_stock: ["DigiKey", "Mouser"], risk_flags: [] }),
      ]) as never;
    });

    renderPage();

    expect(await screen.findByText("Risky")).toBeDefined();
    expect(screen.queryByText("Clean")).toBeNull();

    await userEvent.click(screen.getByLabelText("Show only flagged"));

    expect(await screen.findByText("Clean")).toBeDefined();
    expect(get).toHaveBeenCalledWith("/reports/sourcing-risk?only_with_flags=false");
  });

  it("sorts by flag count desc by default", async () => {
    vi.spyOn(api, "get").mockResolvedValue(report([
      row({ part_id: "part-clean", name: "Clean", mpn: "CLEAN", distributors_with_stock: ["DigiKey", "Mouser"], risk_flags: [] }),
      row({ part_id: "part-risk", name: "Risky", mpn: "RISKY", risk_flags: ["single_source", "lead_time_long"] }),
    ]));

    renderPage();

    await screen.findByText("Risky");
    await waitFor(() => {
      const bodyRows = screen.getAllByRole("row").slice(1);
      expect(within(bodyRows[0]).getByText("Risky")).toBeDefined();
      expect(within(bodyRows[1]).getByText("Clean")).toBeDefined();
    });
  });
});
