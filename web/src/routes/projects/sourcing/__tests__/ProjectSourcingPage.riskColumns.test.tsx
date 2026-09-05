// @vitest-environment jsdom
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { enableBomColumns, mockReads, renderPage, resetProjectSourcingPageTest, sourceBom, sourcingResponse } from "./ProjectSourcingPage.testUtils";

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
  it("renders compliant RoHS data as a green pill once the column is enabled", async () => {
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
              rohs_compliance: [{ region: "EU", is_compliant: true }],
            },
          ],
          risk_flags: [],
        },
      ],
    }));

    renderPage();

    await sourceBom(user);
    await enableBomColumns(user, "RoHS");
    const rohs = screen.getByText("Compliant");
    expect(rohs.className).toContain("text-success");
    expect(rohs.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("keeps the lifecycle column reachable from the column menu and renders Obsolete as red", async () => {
    const user = userEvent.setup();
    mockReads();
    const base = sourcingResponse();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      rows: [
        {
          ...base.rows[0],
          best_offer: {
            ...base.rows[0].best_offer,
            lifecycle_risk: "Obsolete",
          },
        },
      ],
    }));

    renderPage();

    await sourceBom(user);
    const bomRowsTable = screen.getAllByRole("table")[1];
    expect(within(bomRowsTable).queryByRole("columnheader", { name: /Lifecycle/ })).toBeNull();

    await enableBomColumns(user, "Lifecycle");

    expect(within(bomRowsTable).getByRole("columnheader", { name: /Lifecycle/ })).toBeDefined();
    const lifecycle = screen.getByLabelText("Lifecycle risk: Obsolete");
    expect(lifecycle.className).toContain("text-danger");
    expect(lifecycle.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("renders Low lifecycle risk as a green pill in the BOM table", async () => {
    const user = userEvent.setup();
    mockReads();
    const base = sourcingResponse();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      rows: [
        {
          ...base.rows[0],
          best_offer: {
            ...base.rows[0].best_offer,
            lifecycle_risk: " Low risk ",
          },
        },
      ],
    }));

    renderPage();

    await sourceBom(user);
    await enableBomColumns(user, "Lifecycle");
    const lifecycle = screen.getByLabelText("Lifecycle risk: Low risk");
    expect(lifecycle.className).toContain("text-success");
  });

  it("prefixes lifecycle risk tones with distinct hidden icons", async () => {
    const user = userEvent.setup();
    mockReads();
    const base = sourcingResponse();
    const tones = [
      ["Active", "lucide-circle-check"],
      ["Low-Med", "lucide-circle-alert"],
      ["Medium", "lucide-triangle-alert"],
      ["Obsolete", "lucide-octagon-alert"],
      ["Review pending", "lucide-circle"],
    ] as const;
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      rows: tones.map(([risk], index) => ({
        ...base.rows[0],
        project_entry_id: `entry-tone-${index}`,
        part_id: `part-tone-${index}`,
        part_name: `Tone ${index}`,
        mpn: `TONE-${index}`,
        best_offer: {
          ...base.rows[0].best_offer,
          lifecycle_risk: risk,
        },
        risk_flags: [],
      })),
    }));

    renderPage();

    await sourceBom(user);
    await enableBomColumns(user, "Lifecycle");
    for (const [risk, iconClass] of tones) {
      const pill = screen.getByLabelText(`Lifecycle risk: ${risk}`);
      const icon = pill.querySelector("svg");
      expect(icon?.getAttribute("aria-hidden")).toBe("true");
      expect(icon?.getAttribute("class")).toContain(iconClass);
      expect(pill.textContent).toBe(risk);
    }
  });

  it("hides lead time by default but keeps per-row values available from the column menu", async () => {
    const user = userEvent.setup();
    mockReads();
    const base = sourcingResponse();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      rows: [
        ...base.rows,
        {
          ...base.rows[1],
          project_entry_id: "entry-3",
          part_id: "part-3",
          part_name: "Oscillator",
          mpn: "XO-1",
          best_offer: {
            ...base.rows[1].best_offer,
            lead_time_days: null,
          },
          lead_time_days: null,
          risk_flags: [],
        },
      ],
    }));

    renderPage();

    await sourceBom(user);
    const bomRowsTable = screen.getAllByRole("table")[1];
    expect(within(bomRowsTable).queryByRole("columnheader", { name: "Lead time" })).toBeNull();

    await enableBomColumns(user, "Lead time");

    // Columns render in declaration order, so with only Lead time enabled the
    // visible set is part, MPN, Required, On hand, Short, Best offer,
    // Distributor, Est. cost, Lead time, Risk.
    const leadTimeCellText = (rowName: RegExp) =>
      within(within(bomRowsTable).getByRole("row", { name: rowName })).getAllByRole("cell")[8].textContent;

    expect(within(bomRowsTable).getByRole("columnheader", { name: "Lead time" })).toBeDefined();
    expect(leadTimeCellText(/STM32/)).toBe("3 days");
    expect(leadTimeCellText(/Regulator/)).toBe("7 days");
    expect(leadTimeCellText(/Oscillator/)).toBe("—");
  });

});
