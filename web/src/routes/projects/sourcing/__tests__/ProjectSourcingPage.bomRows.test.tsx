// @vitest-environment jsdom
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { clickSource, mockReads, renderPage, resetProjectSourcingPageTest, sourceBom, sourcingResponse } from "./ProjectSourcingPage.testUtils";

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
  it("renders risk pills for each flag returned", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();
    await clickSource();

    expect(await screen.findByText("Single source")).toBeDefined();
    expect(screen.getByText("Long lead time")).toBeDefined();
    expect(screen.getByText("Preferred unmet")).toBeDefined();
    expect(screen.getAllByLabelText("Source: TrustedParts").length).toBeGreaterThan(0);
  });

  it("clicking a BOM row with offers opens the BomDistributorsModal", async () => {
    const user = userEvent.setup();
    mockReads();
    const base = sourcingResponse();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      rows: [
        {
          ...base.rows[0],
          offers: [
            {
              ...base.rows[0].offers[0],
              availability_text: "In Stock",
              quantity_multiple: 5,
              price_breaks: [{ quantity: 1, unit_price: "1.25" }],
              rohs_compliance: [{ region: "EU", is_compliant: true }],
            },
          ],
        },
      ],
    }));

    renderPage();

    await sourceBom(user);
    const bomRowsTable = screen.getAllByRole("table")[1];
    await user.click(within(bomRowsTable).getByRole("row", { name: /Open STM32/ }));

    const dialog = await screen.findByRole("dialog", { name: /STM32 — STM32F103C8T6/ });
    expect(within(dialog).getByText("In Stock")).toBeDefined();
    expect(within(dialog).getByText("EU")).toBeDefined();
  });

  it("clicking an unmatched BOM row does not open the modal", async () => {
    const user = userEvent.setup();
    mockReads();
    const base = sourcingResponse();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      rows: [
        {
          ...base.rows[0],
          offers: [],
          best_offer: null,
          authorized_stock: 0,
          est_extended_cost: null,
          lead_time_days: null,
          reason: "no_offers",
          risk_flags: [],
        },
      ],
    }));

    renderPage();

    await sourceBom(user);
    const bomRowsTable = screen.getAllByRole("table")[1];
    await user.click(within(bomRowsTable).getByRole("row", { name: /STM32/ }));

    expect(screen.queryByRole("dialog", { name: /STM32 — STM32F103C8T6/ })).toBeNull();
  });

  it("splits lifecycle, supply-chain, and RoHS out of the legacy risk column", async () => {
    mockReads();
    const base = sourcingResponse();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      rows: [
        {
          ...base.rows[0],
          best_offer: {
            ...base.rows[0].best_offer,
            lifecycle_risk: "High",
            supply_chain_risk: "Medium",
          },
          offers: [
            {
              ...base.rows[0].offers[0],
              lifecycle_risk: "High",
              supply_chain_risk: "Medium",
              rohs_compliance: [{ region: "EU", is_compliant: false }],
            },
          ],
          risk_flags: [
            "lifecycle_risk_present",
            "supply_chain_risk_present",
            "tariff_affected",
            "rohs_non_compliant",
          ],
        },
      ],
    }));

    renderPage();

    await sourceBom();
    const bomRowsTable = screen.getAllByRole("table")[1];
    expect(within(bomRowsTable).getByRole("columnheader", { name: /Lifecycle/ })).toBeDefined();
    expect(within(bomRowsTable).getByRole("columnheader", { name: /Supply chain/ })).toBeDefined();
    expect(within(bomRowsTable).getByRole("columnheader", { name: "RoHS" })).toBeDefined();

    const lifecycle = screen.getByLabelText("Lifecycle risk: High");
    const supplyChain = screen.getByLabelText("Supply-chain risk: Medium");
    const tariff = screen.getByText("tariff");
    const rohs = screen.getByText("Non-compliant");
    expect(lifecycle.className).toContain("text-danger");
    expect(supplyChain.className).toContain("text-warning");
    expect(tariff.className).toContain("text-warning");
    expect(rohs.className).toContain("text-danger");
    expect(lifecycle.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    expect(supplyChain.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    expect(rohs.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");

    const riskCell = within(bomRowsTable).getByRole("row", { name: /STM32/ }).querySelectorAll("td")[12];
    expect(within(riskCell as HTMLElement).queryByText("lifecycle")).toBeNull();
    expect(within(riskCell as HTMLElement).queryByText("supply chain")).toBeNull();
    expect(within(riskCell as HTMLElement).queryByText("RoHS")).toBeNull();
    expect(screen.getByLabelText("TrustedParts did not find a compliant RoHS region for this BOM line.")).toBeDefined();
  });

  it("opens distinct 4-row risk legends from the Lifecycle and Supply chain headers", async () => {
    const user = userEvent.setup();
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    await sourceBom(user);
    const bomRowsTable = screen.getAllByRole("table")[1];
    const lifecycleHeader = within(bomRowsTable).getByRole("columnheader", { name: /Lifecycle/ });
    const supplyChainHeader = within(bomRowsTable).getByRole("columnheader", { name: /Supply chain/ });

    expect(within(lifecycleHeader).getByRole("button", { name: "Show Lifecycle Risk Statuses" })).toBeDefined();

    await user.click(within(lifecycleHeader).getByRole("button", { name: "Show Lifecycle Risk Statuses" }));
    const lifecycleLegend = await screen.findByRole("dialog", { name: "Lifecycle Risk Statuses" });
    expect(within(lifecycleLegend).getAllByRole("listitem")).toHaveLength(4);
    expect(within(lifecycleLegend).getByText("This product is active.")).toBeDefined();
    expect(within(lifecycleLegend).getByText("This product may be EOL (end of life) or NRND.")).toBeDefined();

    await user.click(within(supplyChainHeader).getByRole("button", { name: "Show Supply Chain Risk Statuses" }));
    const supplyChainLegend = await screen.findByRole("dialog", { name: "Supply Chain Risk Statuses" });
    expect(within(supplyChainLegend).getAllByRole("listitem")).toHaveLength(4);
    expect(within(supplyChainLegend).getByText("Available stock with short lead times.")).toBeDefined();
    expect(within(supplyChainLegend).getByText("Limited stock or long lead times.")).toBeDefined();
    expect(within(supplyChainLegend).queryByText("This product is active.")).toBeNull();
  });

});
