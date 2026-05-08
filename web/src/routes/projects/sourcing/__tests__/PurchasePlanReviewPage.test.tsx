// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { api } from "@/lib/api";
import PurchasePlanReviewPage from "../PurchasePlanReviewPage";
import type { PurchasePlan } from "../purchasePlanTypes";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

function plan(overrides: Partial<PurchasePlan> = {}): PurchasePlan {
  return {
    id: "plan-12345678",
    project_id: "project-123",
    build_quantity: 1,
    strategy: "preferred_first",
    country_code: "US",
    currency_code: "USD",
    preferred_distributors: ["DigiKey"],
    status: "refreshed",
    created_at: "2026-05-09T12:00:00+00:00",
    expires_at: "2026-05-15T12:00:00+00:00",
    last_refreshed_at: new Date().toISOString(),
    distributors_used: ["DigiKey", "Mouser"],
    est_total_cost: "25.00",
    worst_lead_time_days: 7,
    unfilled_count: 1,
    lines: [
      {
        id: "line-1",
        project_entry_id: "entry-1",
        part_id: "part-1",
        mpn_searched: "STM32",
        required_qty: 20,
        internal_available_qty: 4,
        shortage_qty: 16,
        selected_distributor: "DigiKey",
        selected_qty: 16,
        selected_unit_price: "1.25",
        selected_currency: "USD",
        selected_packaging: "cut-tape",
        selected_moq: 1,
        selected_lead_time_days: 3,
        selected_url: "https://www.trustedparts.com/stm32",
        risk_flags: ["single_source"],
      },
      {
        id: "line-2",
        project_entry_id: "entry-2",
        part_id: "part-2",
        mpn_searched: "LM1117",
        required_qty: 10,
        internal_available_qty: 0,
        shortage_qty: 10,
        selected_distributor: "Mouser",
        selected_qty: 10,
        selected_unit_price: "0.50",
        selected_currency: "USD",
        selected_packaging: "reel",
        selected_moq: 1,
        selected_lead_time_days: 7,
        selected_url: "https://www.trustedparts.com/lm1117",
        risk_flags: [],
      },
      {
        id: "line-3",
        project_entry_id: "entry-3",
        part_id: "part-3",
        mpn_searched: "NO-STOCK",
        required_qty: 1,
        internal_available_qty: 0,
        shortage_qty: 1,
        selected_distributor: null,
        selected_qty: 0,
        selected_unit_price: null,
        selected_currency: null,
        selected_packaging: null,
        selected_moq: null,
        selected_lead_time_days: null,
        selected_url: null,
        risk_flags: ["no_authorized_stock"],
      },
    ],
    ...overrides,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderPage(initialPlan: PurchasePlan = plan()) {
  render(
    <MemoryRouter
      initialEntries={[
        {
          pathname: "/projects/project-123/purchase-plans/plan-12345678",
          state: {
            plan: initialPlan,
            project: { id: "project-123", name: "Amplifier" },
          },
        },
      ]}
    >
      <Routes>
        <Route
          path="/projects/:projectId/purchase-plans/:planId"
          element={<PurchasePlanReviewPage />}
        />
        <Route path="/orders" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("PurchasePlanReviewPage", () => {
  it("renders summary cards from server response", () => {
    renderPage();

    expect(screen.getByRole("heading", { name: /Purchase plan #\s*plan-123/ })).toBeDefined();
    expect(screen.getByText("Distributors")).toBeDefined();
    expect(screen.getByText("25 USD")).toBeDefined();
    expect(screen.getAllByText("7 days").length).toBeGreaterThan(0);
    expect(screen.getByText("Powered by TrustedParts")).toBeDefined();
  });

  it("renders one distributor group per distributor", () => {
    renderPage();

    expect(screen.getByText("DigiKey")).toBeDefined();
    expect(screen.getByText("Mouser")).toBeDefined();
    expect(screen.getAllByLabelText("Source: TrustedParts").length).toBeGreaterThanOrEqual(2);
  });

  it("unfilled lines render in a separate red card", () => {
    renderPage();

    const section = screen.getByText("Unfilled lines").closest("section");
    expect(section).not.toBeNull();
    expect(within(section as HTMLElement).getByText("NO-STOCK")).toBeDefined();
  });

  it("Refresh prices calls TP-404 and replaces visible state", async () => {
    const refreshed = plan({
      est_total_cost: "12.00",
      distributors_used: ["Arrow"],
      lines: [
        {
          ...plan().lines[0],
          id: "line-new",
          selected_distributor: "Arrow",
          selected_unit_price: "0.75",
        },
      ],
      unfilled_count: 0,
    });
    vi.spyOn(api, "post").mockResolvedValueOnce(refreshed);
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: /Refresh prices/ }));

    await waitFor(() => expect(screen.getByText("Arrow")).toBeDefined());
    expect(api.post).toHaveBeenCalledWith("/sourcing/purchase-plans/plan-12345678/refresh");
    expect(toast.success).toHaveBeenCalledWith("Prices refreshed");
  });

  it("Create draft orders is disabled when last_refreshed_at is null", () => {
    renderPage(plan({ last_refreshed_at: null }));

    const button = screen.getByRole("button", { name: /Create draft orders/ });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("Create draft orders is disabled when last_refreshed_at is > 10 min old", () => {
    const stale = new Date(Date.now() - 11 * 60 * 1000).toISOString();
    renderPage(plan({ last_refreshed_at: stale }));

    const button = screen.getByRole("button", { name: /Create draft orders/ });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Refresh stale (>10 min)")).toBeDefined();
  });

  it("Successful conversion redirects to /orders with toast", async () => {
    vi.spyOn(api, "post").mockResolvedValueOnce({
      orders: [{ id: "order-1", name: "Draft", supplier: "DigiKey", status: "draft", entries: [] }],
    });
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: /Create draft orders/ }));

    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/orders"));
    expect(toast.success).toHaveBeenCalledWith("Created 1 draft orders");
  });
});
