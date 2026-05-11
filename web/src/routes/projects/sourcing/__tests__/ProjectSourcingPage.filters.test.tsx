// @vitest-environment jsdom
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { apiError, clickSource, mockReads, renderPage, resetProjectSourcingPageTest, sourcingResponse } from "./ProjectSourcingPage.testUtils";

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
  it("currency dropdown options are workspace.active_currencies", async () => {
    mockReads({ active_currencies: ["EUR", "JPY"] });
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    const currency = await screen.findByLabelText("Currency") as HTMLSelectElement;
    await waitFor(() => {
      expect([...currency.options].map(option => option.value)).toEqual(["EUR", "JPY"]);
    });
  });

  it("country dropdown defaults to workspace.sourcing_country_code", async () => {
    mockReads({ sourcing_country_code: "DE", active_countries: ["US", "DE"] });
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    const country = await screen.findByLabelText("Country") as HTMLSelectElement;
    await waitFor(() => expect(country.value).toBe("DE"));
  });

  it("does not fire POST on mount", async () => {
    mockReads();
    const post = vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    await screen.findByRole("button", { name: "Source" });
    await waitFor(() => expect((screen.getByRole("button", { name: "Source" }) as HTMLButtonElement).disabled).toBe(false));
    expect(post).not.toHaveBeenCalled();
  });

  it("fires exactly one POST on submit", async () => {
    mockReads();
    const post = vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();
    await clickSource();

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  });

  it("does not refire POST on window focus", async () => {
    mockReads();
    const post = vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();
    await clickSource();
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));

    fireEvent.focus(window);

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  });

  it("does not refire POST on requestBody keystroke before submit", async () => {
    const user = userEvent.setup();
    mockReads();
    const post = vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    await screen.findByRole("button", { name: "Source" });
    await waitFor(() => expect((screen.getByRole("button", { name: "Source" }) as HTMLButtonElement).disabled).toBe(false));
    await user.clear(screen.getByLabelText("Build quantity"));
    await user.type(screen.getByLabelText("Build quantity"), "12");

    expect(post).not.toHaveBeenCalled();
  });

  it("shows error toast on 429", async () => {
    mockReads();
    vi.spyOn(api, "post").mockRejectedValue(apiError(429, "Too Many Requests"));

    renderPage();
    await clickSource();

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Rate limit hit — wait a minute before sourcing again.");
    });
  });

  it("uses structured provider rate-limit codes for sourcing toast", async () => {
    mockReads();
    vi.spyOn(api, "post").mockRejectedValue(
      apiError(502, "TrustedParts rate limit reached", "sourcing.provider_rate_limited"),
    );

    renderPage();
    await clickSource();

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Rate limit hit — wait a minute before sourcing again.");
    });
  });

  it("links currency mismatch errors to workspace settings", async () => {
    mockReads();
    vi.spyOn(api, "post").mockRejectedValue(
      apiError(422, "mixed currencies for distributor DigiKey", "sourcing.currency_mismatch"),
    );

    renderPage();
    await clickSource();

    expect(await screen.findByText("Sourcing returned mixed currencies.")).toBeDefined();
    const link = screen.getByRole("link", { name: "Open workspace settings" });
    expect(link.getAttribute("href")).toBe("/settings/workspace");
    expect(toast.error).toHaveBeenCalledWith("Sourcing returned mixed currencies. Check workspace currency settings.");
  });

  it("distributors multi-select defaults to workspace.sourcing_preferred_distributors", async () => {
    mockReads({
      sourcing_preferred_distributors: ["Mouser", "Arrow"],
      active_distributors: ["DigiKey", "Mouser", "Arrow"],
    });
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    expect((await screen.findByRole("checkbox", { name: "Mouser" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "Arrow" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "DigiKey" }) as HTMLInputElement).checked).toBe(false);
  });

  it("partial-overlap distributors default to saved active intersection", async () => {
    mockReads({
      sourcing_preferred_distributors: ["A", "B", "C"],
      active_distributors: ["A", "B", "X"],
    });
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    expect((await screen.findByRole("checkbox", { name: "A" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "B" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "X" }) as HTMLInputElement).checked).toBe(false);
    expect(await screen.findByText("Workspace preferred distributors are not all active; using active distributors only.")).toBeDefined();
  });

  it("no-overlap distributors fall back to first active distributor", async () => {
    mockReads({
      sourcing_preferred_distributors: ["X", "Y"],
      active_distributors: ["A", "B"],
    });
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    expect((await screen.findByRole("checkbox", { name: "A" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "B" }) as HTMLInputElement).checked).toBe(false);
    expect(await screen.findByText("Workspace preferred distributors are not all active; using active distributors only.")).toBeDefined();
  });

  it("if default not in active list, falls back to first item with warning visible", async () => {
    mockReads({
      sourcing_currency_code: "GBP",
      active_currencies: ["EUR", "JPY"],
    });
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    const currency = await screen.findByLabelText("Currency") as HTMLSelectElement;
    await waitFor(() => expect(currency.value).toBe("EUR"));
    expect(await screen.findByText("Workspace default currency is not active; using EUR.")).toBeDefined();
  });

  it("CapacityBanner renders Total BOM cost when capacity.total_bom_cost is non-null", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();
    await clickSource();

    expect(await screen.findByText("Can build now")).toBeDefined();
    expect(screen.getByText("After purchase")).toBeDefined();
    expect(screen.getByText("3")).toBeDefined();
    expect(screen.getByText("Total BOM cost:")).toBeDefined();
    expect(screen.getByText("30 USD")).toBeDefined();
    expect(screen.getByText("x 2 builds")).toBeDefined();
  });

  it("CapacityBanner renders Cost per 1 BOM when capacity.cost_per_single_bom is non-null", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();
    await clickSource();

    expect(await screen.findByText("Cost per 1 BOM:")).toBeDefined();
    expect(screen.getByText("15 USD")).toBeDefined();
    expect(screen.getByText("one full build")).toBeDefined();
  });

  it("CapacityBanner renders Price to pay when capacity.purchase_to_pay_cost is non-null", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();
    await clickSource();

    expect(await screen.findByText("Price to pay:")).toBeDefined();
    expect(screen.getAllByText("25 USD").length).toBeGreaterThan(0);
    expect(screen.getByText("short qty only, excluding blocking lines")).toBeDefined();
  });

  it("CapacityBanner shows em-dash when both values are null", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      capacity: {
        can_build_now: 0,
        can_build_after_purchase: 0,
        total_bom_cost: null,
        cost_per_single_bom: null,
        purchase_to_pay_cost: null,
        est_purchase_cost: null,
        blocking_lines_now: ["entry-1"],
        blocking_lines_after_purchase: ["entry-1"],
      },
    }));

    renderPage();
    await clickSource();

    expect(await screen.findByText("Total BOM cost:")).toBeDefined();
    expect(screen.getAllByText("no pricing available on any line").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("no non-blocking priced shortages")).toBeDefined();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
  });

});
