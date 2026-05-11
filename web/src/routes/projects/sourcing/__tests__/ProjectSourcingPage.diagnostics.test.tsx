// @vitest-environment jsdom
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
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
  it("409 path renders not-configured card with settings link", async () => {
    mockReads();
    vi.spyOn(api, "post").mockRejectedValue(apiError(409, "sourcing not configured"));

    renderPage();
    await clickSource();

    expect(await screen.findByText("Sourcing not configured.")).toBeDefined();
    expect(screen.getByRole("status", { name: "Sourcing diagnostics" })).toBeDefined();
    expect(screen.getByText("Sourcing cannot run until TrustedParts credentials and workspace defaults are configured.")).toBeDefined();
    const link = screen.getByRole("link", { name: "Open Settings → Sourcing" });
    expect(link.getAttribute("href")).toBe("/settings/workspace");
  });

  it("renders diagnostic guidance when every BOM row is missing an MPN", async () => {
    mockReads();
    const base = sourcingResponse();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      rows: base.rows.map(row => ({
        ...row,
        mpn: null,
        authorized_stock: 0,
        offers: [],
        best_offer: null,
        est_extended_cost: null,
        lead_time_days: null,
        reason: "no_mpn",
        cache_hit: null,
        risk_flags: [],
      })),
    }));

    renderPage();
    await clickSource();

    expect(await screen.findByRole("status", { name: "Sourcing diagnostics" })).toBeDefined();
    expect(screen.getByText("BOM lines need manufacturer part numbers.")).toBeDefined();
    expect(screen.getByText("Add MPNs to these parts, then source the BOM again.")).toBeDefined();
    expect(screen.getByRole("link", { name: "Edit BOM" }).getAttribute("href")).toBe("/projects/project-123/import");
  });

  it("renders cached empty-result guidance with a Refresh prices action", async () => {
    const user = userEvent.setup();
    mockReads();
    const base = sourcingResponse();
    const post = vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      rows: base.rows.map(row => ({
        ...row,
        authorized_stock: 0,
        offers: [],
        best_offer: null,
        est_extended_cost: null,
        lead_time_days: null,
        reason: "no_offers",
        cache_hit: true,
        risk_flags: [],
      })),
    }));

    renderPage();
    await clickSource(user);

    expect(await screen.findByText("Only cached no-offer results were available.")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Refresh prices" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  });

  it("renders FX guidance when unavailable is represented in unmatched rows", async () => {
    mockReads();
    const base = sourcingResponse();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      rows: base.rows.map(row => ({
        ...row,
        offers: [{ ...row.offers[0], unit_price: null }],
        best_offer: null,
        est_extended_cost: null,
        lead_time_days: null,
        reason: "no_offers",
        cache_hit: false,
        fx_status: "unavailable",
        risk_flags: [],
      })),
    }));

    renderPage();
    await clickSource();

    expect(await screen.findByText("Prices were found, but currency conversion is unavailable.")).toBeDefined();
    expect(screen.getByText("Retry later or choose the offer currency while exchange rates are unavailable.")).toBeDefined();
  });

  it("renders generic no-offers diagnostics when all rows are unmatched", async () => {
    mockReads();
    const base = sourcingResponse();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      rows: base.rows.map(row => ({
        ...row,
        authorized_stock: 0,
        offers: [],
        best_offer: null,
        est_extended_cost: null,
        lead_time_days: null,
        reason: "no_offers",
        cache_hit: false,
        risk_flags: [],
      })),
    }));

    renderPage();
    await clickSource();

    expect(await screen.findByText("No matching offers found.")).toBeDefined();
    expect(screen.getByText("TrustedParts returned no authorized offers for the selected country, currency, and distributors.")).toBeDefined();
  });

  it("503 path renders budget banner", async () => {
    mockReads();
    vi.spyOn(api, "post").mockRejectedValue(apiError(503, "sourcing budget exhausted"));

    renderPage();
    await clickSource();

    expect(await screen.findByText("TrustedParts request budget reached for this hour. Retry is paused for 5 minutes.")).toBeDefined();
    const retry = screen.getByRole("button", { name: "Retry Source BOM" }) as HTMLButtonElement;
    await waitFor(() => expect(retry.disabled).toBe(true));
  });

  it("partial flag surfaces the partial badge", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({ partial: true }));

    renderPage();
    await clickSource();

    expect(await screen.findByText("Partial — some chunks served from cache")).toBeDefined();
  });

  it("BOM request body includes currency from workspace settings", async () => {
    mockReads({ sourcing_currency_code: "EUR", active_currencies: ["USD", "EUR"] });
    const post = vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    await sourceBom();
    await waitFor(() => {
      expect(post).toHaveBeenCalledWith(
        `/projects/${projectId}/sourcing`,
        expect.objectContaining({
          build_quantity: 1,
          country: "US",
          currency: "EUR",
          distributors: ["DigiKey", "Mouser"],
        }),
      );
    });
  });

  it("BOM request body sends null currency when workspace has no currency set", async () => {
    mockReads({ sourcing_currency_code: null });
    const post = vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    await sourceBom();
    await waitFor(() => {
      expect(post).toHaveBeenCalledWith(
        `/projects/${projectId}/sourcing`,
        expect.objectContaining({
          build_quantity: 1,
          currency: null,
        }),
      );
    });
  });

});
