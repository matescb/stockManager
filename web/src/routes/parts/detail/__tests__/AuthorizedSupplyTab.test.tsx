// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { ApiError, api } from "@/lib/api";
import { AuthorizedSupplyTab } from "../AuthorizedSupplyTab";

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

const partId = "part-123";
const sourcingQueryKey = ["ws", "ws-1", "part", partId, "sourcing", "EUR"];

function workspaceResponse(currency: string | null = "EUR") {
  return { sourcing_currency_code: currency };
}

function sourcingResponse() {
  return {
    mpn: "STM32F103C8T6",
    offers: [
      {
        mpn: "STM32F103C8T6",
        manufacturer: "STMicroelectronics",
        distributors: [
          {
            name: "DigiKey",
            stock: 42,
            moq: 1,
            packaging: "Tape",
            unit_price: 1.23,
            currency: "EUR",
            price_breaks: [
              { quantity: 1, unit_price: 1.23 },
              { quantity: 10, unit_price: 1.0 },
              { quantity: 100, unit_price: 0.8 },
              { quantity: 1000, unit_price: 0.62 },
            ],
            lead_time_days: 3,
            product_url: "https://www.trustedparts.com/digikey/stm32",
          },
          {
            name: "Mouser",
            stock: 7,
            moq: 10,
            packaging: "Reel",
            unit_price: 1.11,
            currency: "EUR",
            price_breaks: [
              { quantity: 10, unit_price: 1.11 },
              { quantity: 100, unit_price: 0.7 },
              { quantity: 1000, unit_price: 0.65 },
            ],
            lead_time_days: 14,
            product_url: "https://www.trustedparts.com/mouser/stm32",
          },
        ],
        links: { primary: "https://www.trustedparts.com/stm32" },
      },
    ],
    request_id: "tp-req-1",
    powered_by: "TrustedParts" as const,
    fetched_at: "2026-05-08T12:00:00+00:00",
    cache_hit: false,
    links: {
      primary: "https://www.trustedparts.com/",
      attribution: "https://www.trustedparts.com/en/about",
    },
    reason: "ok" as const,
  };
}

function convertedSourcingResponse() {
  const response = sourcingResponse();
  Object.assign(response.offers[0].distributors[0], {
    unit_price: 2.5,
    currency: "USD",
    unit_price_converted: "1.25",
    currency_displayed: "EUR",
    fx_converted: true,
    fx_rate_date: "2026-05-08",
    price_breaks_converted: [{ quantity: 1, unit_price: "1.25" }],
  });
  return response;
}

function mockApiGet(response: unknown, workspaceCurrency: string | null = "EUR") {
  return vi.spyOn(api, "get").mockImplementation((path: string) => {
    if (path === "/workspaces/current") {
      return Promise.resolve(workspaceResponse(workspaceCurrency));
    }
    return Promise.resolve(response);
  });
}

function mockApiGetWithAlertPicker(response: unknown, workspaceCurrency: string | null = "EUR") {
  return vi.spyOn(api, "get").mockImplementation((path: string) => {
    if (path === "/workspaces/current") {
      return Promise.resolve(workspaceResponse(workspaceCurrency));
    }
    if (path === "/workspaces/members") {
      return Promise.resolve([]);
    }
    if (path.startsWith("/parts?")) {
      return Promise.resolve([
        {
          id: partId,
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
          linked_provider: null,
          linked_external_id: null,
          last_refresh_at: null,
          description_locally_edited: false,
          archived_at: null,
          on_hand: 0,
          reserved: 0,
          available: 0,
          image_url: null,
        },
      ]);
    }
    return Promise.resolve(response);
  });
}

function mockApiGetError(error: ApiError, workspaceCurrency: string | null = "EUR") {
  return vi.spyOn(api, "get").mockImplementation((path: string) => {
    if (path === "/workspaces/current") {
      return Promise.resolve(workspaceResponse(workspaceCurrency));
    }
    return Promise.reject(error);
  });
}

function sourcingGetCalls(spy: ReturnType<typeof mockApiGet>) {
  return spy.mock.calls.filter(([path]) => String(path).startsWith(`/parts/${partId}/sourcing`));
}

function apiError(status: number, message: string, extra: Record<string, unknown> = {}) {
  const category =
    status === 409 ? "conflict" : status === 429 ? "rate_limited" : "server_error";
  const body = {
    data: null,
    status: { category, message },
    ...extra,
  };
  return new ApiError(
    status,
    body,
    message,
  );
}

function renderTab(client?: QueryClient) {
  const queryClient = client ?? new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <AuthorizedSupplyTab partId={partId} />
    </QueryClientProvider>,
  );
  return { queryClient, invalidateSpy };
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("AuthorizedSupplyTab", () => {
  it("renders offers and attribution badge for part with MPN", async () => {
    mockApiGet(sourcingResponse());

    renderTab();

    expect(await screen.findByText("Powered by TrustedParts")).toBeDefined();
    expect(screen.getByLabelText("Source: TrustedParts")).toBeDefined();
    const table = screen.getByRole("table");
    expect(within(table).getByText("DigiKey")).toBeDefined();
    expect(within(table).getByText("Mouser")).toBeDefined();
    expect(within(table).getAllByRole("button", { name: "Add to order" })).toHaveLength(2);
    expect(screen.getByText("42")).toBeDefined();
    expect(screen.getByText("Tape")).toBeDefined();
  });

  it("renders no-mpn empty state and never calls the API path with empty mpn", async () => {
    const getSpy = mockApiGet({
      offers: [],
      reason: "no_mpn",
      cache_hit: null,
    });
    const postSpy = vi.spyOn(api, "post").mockResolvedValue({});

    renderTab();

    expect(await screen.findByText("Add an MPN to this part to see authorized-distributor offers.")).toBeDefined();
    expect(getSpy).toHaveBeenCalledWith(`/parts/${partId}/sourcing?currency=EUR`, expect.any(Object));
    expect(postSpy).not.toHaveBeenCalled();
  });

  it("refresh button invalidates the query", async () => {
    const user = userEvent.setup();
    mockApiGet(sourcingResponse());
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());
    const { invalidateSpy } = renderTab();

    await screen.findByText("Powered by TrustedParts");
    await user.click(screen.getByRole("button", { name: "Refresh live" }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(`/parts/${partId}/sourcing/refresh`, {});
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: sourcingQueryKey });
  });

  it("Set-alert button opens modal with part_id pre-filled", async () => {
    const user = userEvent.setup();
    mockApiGetWithAlertPicker(sourcingResponse());
    vi.spyOn(api, "post").mockResolvedValue({ id: "alert-1" });

    renderTab();

    await screen.findByText("Powered by TrustedParts");
    await user.click(screen.getByRole("button", { name: "Set alert" }));

    expect(await screen.findByRole("dialog", { name: "Set alert on this part" })).toBeDefined();
    expect((screen.getByRole("radio", { name: /STM32/ }) as HTMLInputElement).checked).toBe(true);
  });

  it("distributor filter narrows visible rows", async () => {
    const user = userEvent.setup();
    const getSpy = mockApiGet(sourcingResponse());

    renderTab();

    await screen.findByText("Powered by TrustedParts");
    const filter = screen.getByLabelText("Distributor filter");
    await user.selectOptions(filter, ["Mouser"]);

    const table = screen.getByRole("table");
    expect(within(table).queryByText("DigiKey")).toBeNull();
    expect(within(table).getByText("Mouser")).toBeDefined();
    expect(sourcingGetCalls(getSpy)).toHaveLength(1);
  });

  it("409 path shows admin-prompt card", async () => {
    mockApiGetError(apiError(409, "sourcing not configured"));

    renderTab();

    expect(
      await screen.findByText("Sourcing not configured. Ask a workspace admin to set TrustedParts credentials in Settings → Sourcing."),
    ).toBeDefined();
  });

  it("503 path shows budget banner", async () => {
    mockApiGetError(apiError(503, "sourcing budget exhausted"));

    renderTab();

    expect(await screen.findByText("TrustedParts request budget reached for this hour — try again later.")).toBeDefined();
  });

  it("429 refresh path shows retry-after banner", async () => {
    const user = userEvent.setup();
    mockApiGet(sourcingResponse());
    vi.spyOn(api, "post").mockRejectedValue(
      apiError(429, "rate limit exceeded", { retry_after_seconds: 17 }),
    );

    renderTab();

    await screen.findByText("Powered by TrustedParts");
    await user.click(screen.getByRole("button", { name: "Refresh live" }));

    expect(
      await screen.findByText("TrustedParts refresh rate limit reached — try again in 17 seconds."),
    ).toBeDefined();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("502 path shows unavailable toast with retry action", async () => {
    mockApiGetError(apiError(502, "TrustedParts request timed out"));

    renderTab();

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        "TrustedParts unavailable. Retry?",
        expect.objectContaining({
          action: expect.objectContaining({ label: "Retry" }),
        }),
      );
    });
    expect(screen.getByRole("button", { name: "Retry TrustedParts" })).toBeDefined();
  });

  it("attribution link does not have nofollow", async () => {
    mockApiGet(sourcingResponse());

    renderTab();

    const attribution = await screen.findByRole("link", { name: "Powered by TrustedParts" });
    expect(attribution.getAttribute("href")).toBe("https://www.trustedparts.com/");
    expect(attribution.getAttribute("rel") ?? "").not.toContain("nofollow");

    const table = screen.getByRole("table");
    const offerLink = within(table).getAllByRole("link", { name: /Open/ })[0];
    expect(offerLink.getAttribute("rel") ?? "").not.toContain("nofollow");
  });

  it("flag rendered on rows with fx_converted=true", async () => {
    mockApiGet(convertedSourcingResponse());

    renderTab();

    expect(await screen.findByText("1.25 EUR")).toBeDefined();
    expect(
      screen.getByLabelText("Converted from 2.5 USD via ECB daily rate (2026-05-08)"),
    ).toBeDefined();
  });

  it("tooltip shows original currency and rate date", async () => {
    mockApiGet(convertedSourcingResponse());

    renderTab();

    const badge = await screen.findByLabelText(
      "Converted from 2.5 USD via ECB daily rate (2026-05-08)",
    );
    expect(badge.getAttribute("title")).toBe(
      "Converted from 2.5 USD via ECB daily rate (2026-05-08)",
    );
  });

  it("fx_status warning rendered at top of table when present", async () => {
    const response = { ...sourcingResponse(), fx_status: "unavailable" as const };
    mockApiGet(response);

    renderTab();

    expect(
      await screen.findByText("FX conversion unavailable for some rows — showing native currency."),
    ).toBeDefined();
  });

  it("legacy view when workspace currency is null", async () => {
    const getSpy = mockApiGet(sourcingResponse(), null);

    renderTab();

    expect(await screen.findByText("1.23 EUR")).toBeDefined();
    expect(screen.queryByLabelText(/Converted from/)).toBeNull();
    expect(getSpy).toHaveBeenCalledWith(`/parts/${partId}/sourcing`, expect.any(Object));
  });

  it("quantity preset 100 recomputes unit price across rows", async () => {
    const user = userEvent.setup();
    mockApiGet(sourcingResponse());

    renderTab();

    await screen.findByText("Powered by TrustedParts");
    expect(screen.queryByText("Unit price @ 100")).toBeNull();

    await user.click(screen.getByRole("button", { name: "100" }));

    expect(screen.getByRole("columnheader", { name: "Unit price @ 100" })).toBeDefined();
    expect(screen.getByRole("columnheader", { name: "Extended @ 100" })).toBeDefined();
    expect(screen.getByText("0.8 EUR")).toBeDefined();
    expect(screen.getByText("0.7 EUR")).toBeDefined();
    expect(screen.getByText("80 EUR")).toBeDefined();
    expect(screen.getByText("70 EUR")).toBeDefined();
  });

  it("custom quantity input is applied on blur", async () => {
    const user = userEvent.setup();
    mockApiGet(sourcingResponse());

    renderTab();

    await screen.findByText("Powered by TrustedParts");
    const customInput = screen.getByLabelText("Custom:");
    await user.clear(customInput);
    await user.type(customInput, "25");

    expect(screen.queryByText("Unit price @ 25")).toBeNull();

    await user.tab();

    expect(screen.getByRole("columnheader", { name: "Unit price @ 25" })).toBeDefined();
    expect(screen.getByText("25 EUR")).toBeDefined();
    expect(screen.getByText("27.75 EUR")).toBeDefined();
  });

  it("quantity change does not refetch", async () => {
    const user = userEvent.setup();
    const getSpy = mockApiGet(sourcingResponse());

    renderTab();

    await screen.findByText("Powered by TrustedParts");
    await user.click(screen.getByRole("button", { name: "1,000" }));
    await user.click(screen.getByRole("button", { name: "10" }));

    expect(sourcingGetCalls(getSpy)).toHaveLength(1);
  });

  it("below-MOQ rendering is visible at low quantities", async () => {
    const user = userEvent.setup();
    mockApiGet(sourcingResponse());

    renderTab();

    await screen.findByText("Powered by TrustedParts");
    const customInput = screen.getByLabelText("Custom:");
    await user.clear(customInput);
    await user.type(customInput, "5");
    await user.tab();

    expect(screen.getByRole("columnheader", { name: "Unit price @ 5" })).toBeDefined();
    expect(screen.getByText("Below MOQ")).toBeDefined();
  });

  it("sorts by unit price at selected quantity", async () => {
    const user = userEvent.setup();
    mockApiGet(sourcingResponse());

    renderTab();

    await screen.findByText("Powered by TrustedParts");
    await user.click(screen.getByRole("button", { name: "100" }));
    await user.click(screen.getByRole("columnheader", { name: "Unit price @ 100" }));

    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("Mouser")).toBeDefined();
    expect(within(rows[1]).getByText("DigiKey")).toBeDefined();
  });
});
