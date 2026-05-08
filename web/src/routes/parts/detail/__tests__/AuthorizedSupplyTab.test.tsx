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
const sourcingQueryKey = ["ws", "ws-1", "part", partId, "sourcing"];

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
    vi.spyOn(api, "get").mockResolvedValue(sourcingResponse());

    renderTab();

    expect(await screen.findByText("Powered by TrustedParts")).toBeDefined();
    expect(screen.getByLabelText("Source: TrustedParts")).toBeDefined();
    const table = screen.getByRole("table");
    expect(within(table).getByText("DigiKey")).toBeDefined();
    expect(within(table).getByText("Mouser")).toBeDefined();
    expect(screen.getByText("42")).toBeDefined();
    expect(screen.getByText("Tape")).toBeDefined();
  });

  it("renders no-mpn empty state and never calls the API path with empty mpn", async () => {
    const getSpy = vi.spyOn(api, "get").mockResolvedValue({
      offers: [],
      reason: "no_mpn",
      cache_hit: null,
    });
    const postSpy = vi.spyOn(api, "post").mockResolvedValue({});

    renderTab();

    expect(await screen.findByText("Add an MPN to this part to see authorized-distributor offers.")).toBeDefined();
    expect(getSpy).toHaveBeenCalledWith(`/parts/${partId}/sourcing`, expect.any(Object));
    expect(postSpy).not.toHaveBeenCalled();
  });

  it("refresh button invalidates the query", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockResolvedValue(sourcingResponse());
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());
    const { invalidateSpy } = renderTab();

    await screen.findByText("Powered by TrustedParts");
    await user.click(screen.getByRole("button", { name: "Refresh live" }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(`/parts/${partId}/sourcing/refresh`, {});
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: sourcingQueryKey });
  });

  it("distributor filter narrows visible rows", async () => {
    const user = userEvent.setup();
    const getSpy = vi.spyOn(api, "get").mockResolvedValue(sourcingResponse());

    renderTab();

    await screen.findByText("Powered by TrustedParts");
    const filter = screen.getByLabelText("Distributor filter");
    await user.selectOptions(filter, ["Mouser"]);

    const table = screen.getByRole("table");
    expect(within(table).queryByText("DigiKey")).toBeNull();
    expect(within(table).getByText("Mouser")).toBeDefined();
    expect(getSpy).toHaveBeenCalledTimes(1);
  });

  it("409 path shows admin-prompt card", async () => {
    vi.spyOn(api, "get").mockRejectedValue(apiError(409, "sourcing not configured"));

    renderTab();

    expect(
      await screen.findByText("Sourcing not configured. Ask a workspace admin to set TrustedParts credentials in Settings → Sourcing."),
    ).toBeDefined();
  });

  it("503 path shows budget banner", async () => {
    vi.spyOn(api, "get").mockRejectedValue(apiError(503, "sourcing budget exhausted"));

    renderTab();

    expect(await screen.findByText("TrustedParts request budget reached for this hour — try again later.")).toBeDefined();
  });

  it("429 refresh path shows retry-after banner", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockResolvedValue(sourcingResponse());
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
    vi.spyOn(api, "get").mockRejectedValue(apiError(502, "TrustedParts request timed out"));

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
    vi.spyOn(api, "get").mockResolvedValue(sourcingResponse());

    renderTab();

    const attribution = await screen.findByRole("link", { name: "Powered by TrustedParts" });
    expect(attribution.getAttribute("href")).toBe("https://www.trustedparts.com/");
    expect(attribution.getAttribute("rel") ?? "").not.toContain("nofollow");

    const table = screen.getByRole("table");
    const offerLink = within(table).getAllByRole("link", { name: /Open/ })[0];
    expect(offerLink.getAttribute("rel") ?? "").not.toContain("nofollow");
  });
});
