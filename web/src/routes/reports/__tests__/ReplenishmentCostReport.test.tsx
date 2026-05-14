// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import ReplenishmentCostReport from "../ReplenishmentCostReport";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

function reportResponse(overrides: Record<string, unknown> = {}) {
  return {
    rows: [
      {
        part_id: "part-1",
        name: "Capacitor",
        manufacturer: "Murata",
        mpn: "CAP-10UF",
        on_hand: 10,
        currency: "EUR",
        historical_costs: [{ currency: "EUR", value: "5.000000" }],
        historical_cost: "5.000000",
        replacement_unit_price: "0.75",
        replacement_cost: "7.50",
        replacement_currency: "EUR",
        delta_abs: "2.500000",
        delta_pct: "50.00",
        reason: null,
        source: "trustedparts" as const,
      },
      {
        part_id: "part-2",
        name: "Regulator",
        manufacturer: "TI",
        mpn: "REG-3V3",
        on_hand: 8,
        currency: "EUR",
        historical_costs: [{ currency: "USD", value: "4.000000" }],
        historical_cost: null,
        replacement_unit_price: "1.00",
        replacement_cost: "8.00",
        replacement_currency: "EUR",
        delta_abs: null,
        delta_pct: null,
        reason: "currency_mismatch" as const,
        source: "trustedparts" as const,
      },
    ],
    totals: [
      {
        currency: "EUR",
        historical_cost: "5.000000",
        replacement_cost: "15.50",
        delta_abs: "10.500000",
      },
    ],
    sourcing_status: {
      state: "ok",
      message: null,
      fetched_at: "2026-05-08T12:00:00+00:00",
      cache_hit: false,
      partial: false,
      powered_by: "TrustedParts" as const,
      links: {
        primary: "https://www.trustedparts.com/",
        attribution: "https://www.trustedparts.com/en/about",
      },
    },
    ...overrides,
  };
}

function renderReport() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ReplenishmentCostReport />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("ReplenishmentCostReport", () => {
  it("renders rows", async () => {
    vi.spyOn(api, "get").mockResolvedValue(reportResponse());

    renderReport();

    expect(await screen.findByText("Capacitor")).toBeDefined();
    expect(screen.getByText("CAP-10UF")).toBeDefined();
    expect(screen.getByText("Powered by TrustedParts")).toBeDefined();
    expect(screen.getAllByText("TrustedParts").length).toBeGreaterThan(0);
    expect(screen.getAllByText("EUR").length).toBeGreaterThan(0);
  });

  it("currency mismatch row shows reason chip", async () => {
    vi.spyOn(api, "get").mockResolvedValue(reportResponse());

    renderReport();

    const row = await screen.findByText("Regulator");
    const tableRow = row.closest("tr");
    expect(tableRow).not.toBeNull();
    expect(within(tableRow as HTMLTableRowElement).getByText("Currency mismatch")).toBeDefined();
  });

  it("sort dropdown rerenders", async () => {
    const getSpy = vi.spyOn(api, "get").mockResolvedValue(reportResponse());
    const user = userEvent.setup();

    renderReport();
    await screen.findByText("Capacitor");
    await user.selectOptions(screen.getByLabelText("Sort"), "name");

    expect(await screen.findByText("Name asc")).toBeDefined();
    expect(getSpy).toHaveBeenCalledWith(
      "/reports/replenishment-cost?sort=delta_pct",
      expect.any(Object),
    );
    expect(getSpy).toHaveBeenCalledWith(
      "/reports/replenishment-cost?sort=name",
      expect.any(Object),
    );
  });
});
