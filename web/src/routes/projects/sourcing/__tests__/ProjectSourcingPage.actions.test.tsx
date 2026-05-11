// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { SourceBomButton } from "../SourceBomButton";
import { apiError, clickSource, mockReads, projectId, renderPage, resetProjectSourcingPageTest, sourceBom, sourcingResponse } from "./ProjectSourcingPage.testUtils";

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

beforeEach(resetProjectSourcingPageTest);

describe("ProjectSourcingPage", () => {
  it("renders BOM offer prices from converted display fields when present", async () => {
    mockReads({ sourcing_currency_code: "EUR", active_currencies: ["EUR", "USD"] });
    const base = sourcingResponse();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      rows: [
        {
          ...base.rows[0],
          best_offer: {
            ...(base.rows[0].best_offer as Record<string, unknown>),
            unit_price: "2.00",
            currency: "USD",
            unit_price_converted: "1.00",
            currency_displayed: "EUR",
            fx_converted: true,
          },
          est_extended_cost: "20.00",
        },
      ],
    }));

    renderPage();
    await clickSource();

    expect(await screen.findByText("1 EUR")).toBeDefined();
    expect(screen.queryByText("2 USD")).toBeNull();
    expect(screen.getByText("20 USD")).toBeDefined();
  });

  it("Generate purchase plan posts default strategy from current sourcing filters", async () => {
    const user = userEvent.setup();
    mockReads();
    const post = vi.spyOn(api, "post");
    post.mockResolvedValueOnce(sourcingResponse());
    post.mockResolvedValueOnce({
      id: "plan-1",
      project_id: projectId,
      build_quantity: 1,
      strategy: "preferred_first",
      status: "draft",
      created_at: "2026-05-09T12:00:00+00:00",
      expires_at: "2026-05-15T12:00:00+00:00",
      lines: [],
      distributors_used: [],
      unfilled_count: 0,
    });

    renderPage();

    await sourceBom(user);
    await user.click(screen.getByRole("button", { name: "Generate purchase plan" }));
    await user.click(screen.getByRole("button", { name: "Generate" }));

    expect(await screen.findByTestId("plan-route")).toBeDefined();
    await waitFor(() => {
      expect(post).toHaveBeenLastCalledWith(
        `/projects/${projectId}/purchase-plan`,
        expect.objectContaining({
          build_quantity: 1,
          strategy: "preferred_first",
          country: "US",
          currency: "USD",
          distributors: ["DigiKey", "Mouser"],
        }),
      );
    });
  });

  it("Set BOM-buyable alert opens modal with project and build quantity pre-filled", async () => {
    const user = userEvent.setup();
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    await screen.findByLabelText("Build quantity");
    await user.clear(screen.getByLabelText("Build quantity"));
    await user.type(screen.getByLabelText("Build quantity"), "12");
    await user.click(screen.getByRole("button", { name: "Set BOM-buyable alert" }));

    const dialog = await screen.findByRole("dialog", { name: "Set BOM-buyable alert" });
    expect((within(dialog).getByLabelText("Project") as HTMLSelectElement).value).toBe(projectId);
    expect((within(dialog).getByLabelText("Build quantity") as HTMLInputElement).value).toBe("12");
  });

  it("Source BOM button is disabled when workspace lacks sourcing creds", async () => {
    mockReads({ has_sourcing_api_key: false });
    const client = new QueryClient({
      defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <SourceBomButton projectId={projectId} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const button = await screen.findByRole("button", { name: "Source BOM" });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(true));
    expect(button.getAttribute("title")).toBe("Sourcing not configured");
    expect(screen.getByText("Sourcing not configured")).toBeDefined();
  });

  it("502 path shows toast and retry button", async () => {
    mockReads();
    vi.spyOn(api, "post").mockRejectedValue(apiError(502, "TrustedParts request timed out"));

    renderPage();
    await clickSource();

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Something went wrong. Try again, or refresh.");
    });
    expect(screen.getByRole("button", { name: "Retry Source BOM" })).toBeDefined();
  });
});
