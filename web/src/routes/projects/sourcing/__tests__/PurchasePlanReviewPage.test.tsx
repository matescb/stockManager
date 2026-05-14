// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { ApiError, api } from "@/lib/api";
import PurchasePlanReviewPage from "../PurchasePlanReviewPage";
import type { PurchasePlan } from "../purchasePlanTypes";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
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
        available_offers: [
          {
            mpn: "STM32",
            distributor: "DigiKey",
            stock: 200,
            unit_price: "1.25",
            currency: "USD",
            packaging: "cut-tape",
            moq: 1,
            lead_time_days: 3,
            price_breaks: [],
            url: "https://www.trustedparts.com/stm32",
          },
          {
            mpn: "STM32",
            distributor: "Arrow",
            stock: 50,
            unit_price: "2.05",
            currency: "USD",
            packaging: "tray",
            moq: 5,
            lead_time_days: 2,
            price_breaks: [],
            url: "https://www.trustedparts.com/stm32-arrow",
          },
        ],
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
        available_offers: [],
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
        available_offers: [],
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

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderPage({
  initialPlan = plan(),
  client = makeQueryClient(),
}: {
  initialPlan?: PurchasePlan | null;
  client?: QueryClient;
} = {}) {
  const view = render(
    <QueryClientProvider client={client}>
      <PurchasePlanRoutes initialPlan={initialPlan} />
    </QueryClientProvider>,
  );
  return { ...view, client };
}

function PurchasePlanRoutes({ initialPlan }: { initialPlan?: PurchasePlan | null }) {
  return (
    <MemoryRouter
      initialEntries={[
        {
          pathname: "/projects/project-123/purchase-plans/plan-12345678",
          state: initialPlan != null
            ? {
                plan: initialPlan,
                project: { id: "project-123", name: "Amplifier" },
              }
            : undefined,
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
    </MemoryRouter>
  );
}

async function selectArrowOffer() {
  await userEvent.click(screen.getAllByRole("button", { name: "Override" })[0]);
  const dialog = screen.getByRole("dialog", { name: "Override offer" });
  const arrowRow = within(dialog).getByText("Arrow").closest("tr");
  expect(arrowRow).not.toBeNull();
  await userEvent.click(within(arrowRow as HTMLElement).getByRole("button", { name: "Select" }));
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("PurchasePlanReviewPage", () => {
  it("deep-link to /purchase-plans/:id renders the plan", async () => {
    vi.spyOn(api, "get").mockResolvedValueOnce(plan());

    renderPage({ initialPlan: null });

    expect(await screen.findByRole("heading", { name: /Purchase plan #\s*plan-123/ })).toBeDefined();
    expect(api.get).toHaveBeenCalledWith(
      "/projects/project-123/purchase-plans/plan-12345678",
      expect.any(Object),
    );
  });

  it("reload preserves the plan view from the fresh query cache", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValueOnce(plan());
    const client = makeQueryClient();

    const first = renderPage({ initialPlan: null, client });
    expect(await screen.findByRole("heading", { name: /Purchase plan #\s*plan-123/ })).toBeDefined();
    first.unmount();

    renderPage({ initialPlan: null, client });

    expect(await screen.findByRole("heading", { name: /Purchase plan #\s*plan-123/ })).toBeDefined();
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("error state renders banner", async () => {
    const apiError = new ApiError(
      500,
      { data: null, status: { category: "server_error", message: "plan exploded" } },
      "Internal Server Error",
    );
    apiError.userMessage = "Purchase plan unavailable.";
    vi.spyOn(api, "get").mockRejectedValueOnce(apiError);

    renderPage({ initialPlan: null });

    expect(await screen.findByText("Could not load purchase plan")).toBeDefined();
    expect(screen.getByText("Purchase plan unavailable.")).toBeDefined();
  });

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

  it("prunes overrides whose offer disappears after refresh", async () => {
    const base = plan();
    const refreshed = plan({
      lines: [
        {
          ...base.lines[0],
          selected_distributor: "DigiKey",
          selected_qty: 16,
          selected_unit_price: "1.15",
          available_offers: base.lines[0].available_offers?.slice(0, 1) ?? [],
        },
        base.lines[1],
      ],
      unfilled_count: 0,
    });
    const post = vi.spyOn(api, "post")
      .mockResolvedValueOnce(refreshed)
      .mockResolvedValueOnce({
        orders: [{ id: "order-1", name: "Draft", supplier: "DigiKey", status: "draft", entries: [] }],
      });
    renderPage({ initialPlan: base });

    await selectArrowOffer();
    await userEvent.click(screen.getByRole("button", { name: /Refresh prices/ }));

    await waitFor(() => {
      expect(toast.info).toHaveBeenCalledWith(
        "Removed 1 override no longer available after refresh.",
      );
    });

    await userEvent.click(screen.getByRole("button", { name: /Create draft orders/ }));

    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/orders"));
    expect(post).toHaveBeenNthCalledWith(2, "/sourcing/purchase-plans/plan-12345678/orders", {
      overrides: {},
    });
  });

  it("keeps overrides whose offer is still present after refresh", async () => {
    const refreshed = plan({
      est_total_cost: "18.40",
      lines: [
        {
          ...plan().lines[0],
          selected_distributor: "DigiKey",
          selected_qty: 16,
          selected_unit_price: "1.15",
        },
        plan().lines[1],
      ],
      unfilled_count: 0,
    });
    const post = vi.spyOn(api, "post")
      .mockResolvedValueOnce(refreshed)
      .mockResolvedValueOnce({
        orders: [{ id: "order-1", name: "Draft", supplier: "Arrow", status: "draft", entries: [] }],
      });
    renderPage();

    await selectArrowOffer();
    await userEvent.click(screen.getByRole("button", { name: /Refresh prices/ }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Prices refreshed"));

    await userEvent.click(screen.getByRole("button", { name: /Create draft orders/ }));

    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/orders"));
    expect(post).toHaveBeenNthCalledWith(2, "/sourcing/purchase-plans/plan-12345678/orders", {
      overrides: {
        "line-1": {
          selected_distributor: "Arrow",
          selected_qty: 16,
          selected_unit_price: "2.05",
          selected_currency: "USD",
        },
      },
    });
    expect(toast.info).not.toHaveBeenCalled();
  });

  it("does not prune when refresh fails", async () => {
    const apiError = new ApiError(
      502,
      { data: null, status: { category: "trustedparts_unavailable", message: "TrustedParts unavailable" } },
      "Bad Gateway",
    );
    apiError.userMessage = "TrustedParts unavailable. Retry later.";
    const post = vi.spyOn(api, "post")
      .mockRejectedValueOnce(apiError)
      .mockResolvedValueOnce({
        orders: [{ id: "order-1", name: "Draft", supplier: "Arrow", status: "draft", entries: [] }],
      });
    renderPage();

    await selectArrowOffer();
    await userEvent.click(screen.getByRole("button", { name: /Refresh prices/ }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("TrustedParts unavailable. Retry later."));
    expect(toast.info).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /Create draft orders/ }));

    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/orders"));
    expect(post).toHaveBeenNthCalledWith(2, "/sourcing/purchase-plans/plan-12345678/orders", {
      overrides: {
        "line-1": {
          selected_distributor: "Arrow",
          selected_qty: 16,
          selected_unit_price: "2.05",
          selected_currency: "USD",
        },
      },
    });
  });

  it("uses sourcing.plan_stale code to highlight refresh after conversion failure", async () => {
    const apiError = new ApiError(
      409,
      {
        data: null,
        status: { category: "conflict", message: "plan refresh is stale; refresh again before conversion" },
        code: "sourcing.plan_stale",
      },
      "plan refresh is stale; refresh again before conversion",
    );
    vi.spyOn(api, "post").mockRejectedValueOnce(apiError);
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: /Create draft orders/ }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        "Prices are stale. Refresh prices before creating draft orders.",
      );
    });
    expect(screen.getByRole("button", { name: /Refresh prices/ }).className).toContain("border-warning");
  });

  it("Create draft orders is disabled when last_refreshed_at is null", () => {
    renderPage({ initialPlan: plan({ last_refreshed_at: null }) });

    const button = screen.getByRole("button", { name: /Create draft orders/ });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("Create draft orders is disabled when last_refreshed_at is > 10 min old", () => {
    const stale = new Date(Date.now() - 11 * 60 * 1000).toISOString();
    renderPage({ initialPlan: plan({ last_refreshed_at: stale }) });

    const button = screen.getByRole("button", { name: /Create draft orders/ });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Refresh stale (>10 min)")).toBeDefined();
  });

  it("Successful conversion redirects to /orders with toast", async () => {
    vi.spyOn(api, "post").mockResolvedValueOnce({
      orders: [{ id: "order-1", name: "Draft", supplier: "DigiKey", status: "draft", entries: [] }],
    });
    const { client } = renderPage();

    await userEvent.click(screen.getByRole("button", { name: /Create draft orders/ }));

    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/orders"));
    expect(api.post).toHaveBeenCalledWith("/sourcing/purchase-plans/plan-12345678/orders", { overrides: {} });
    expect(toast.success).toHaveBeenCalledWith("Created 1 draft orders");
    expect(client.getQueryData(["ws", "ws-1", "purchase-plan", "plan-12345678"])).toBeUndefined();
  });

  it("selects an alternate cached offer and sends it as a conversion override", async () => {
    vi.spyOn(api, "post").mockResolvedValueOnce({
      orders: [{ id: "order-1", name: "Draft", supplier: "Arrow", status: "draft", entries: [] }],
    });
    renderPage();

    await selectArrowOffer();

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("Arrow")).toBeDefined();
    expect(screen.getByText("2.05 USD")).toBeDefined();
    expect(screen.getByText("2 days")).toBeDefined();

    await userEvent.click(screen.getByRole("button", { name: /Create draft orders/ }));

    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/orders"));
    expect(api.post).toHaveBeenCalledWith("/sourcing/purchase-plans/plan-12345678/orders", {
      overrides: {
        "line-1": {
          selected_distributor: "Arrow",
          selected_qty: 16,
          selected_unit_price: "2.05",
          selected_currency: "USD",
        },
      },
    });
  });

  it("preserves selected overrides after a failed conversion", async () => {
    const post = vi.spyOn(api, "post")
      .mockRejectedValueOnce(new Error("conversion failed"))
      .mockResolvedValueOnce({
        orders: [{ id: "order-1", name: "Draft", supplier: "Arrow", status: "draft", entries: [] }],
      });
    renderPage();

    await selectArrowOffer();
    await userEvent.click(screen.getByRole("button", { name: /Create draft orders/ }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Could not create draft orders"));
    expect(screen.getByText("Arrow")).toBeDefined();

    await userEvent.click(screen.getByRole("button", { name: /Create draft orders/ }));

    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/orders"));
    expect(post).toHaveBeenLastCalledWith("/sourcing/purchase-plans/plan-12345678/orders", {
      overrides: {
        "line-1": {
          selected_distributor: "Arrow",
          selected_qty: 16,
          selected_unit_price: "2.05",
          selected_currency: "USD",
        },
      },
    });
  });
});
