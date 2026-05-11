// @vitest-environment jsdom
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { mockReads, renderPage, resetProjectSourcingPageTest, sourceBom, sourcingResponse } from "./ProjectSourcingPage.testUtils";

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
  it("renders compliant RoHS data as a green pill", async () => {
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

    await sourceBom();
    const rohs = screen.getByText("Compliant");
    expect(rohs.className).toContain("text-success");
    expect(rohs.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("lifecycle column is visible by default and renders Obsolete as red", async () => {
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

    await sourceBom();
    const bomRowsTable = screen.getAllByRole("table")[1];
    expect(within(bomRowsTable).getByRole("columnheader", { name: /Lifecycle/ })).toBeDefined();
    const lifecycle = screen.getByLabelText("Lifecycle risk: Obsolete");
    expect(lifecycle.className).toContain("text-danger");
    expect(lifecycle.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("renders Low lifecycle risk as a green pill in the BOM table", async () => {
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

    await sourceBom();
    const lifecycle = screen.getByLabelText("Lifecycle risk: Low risk");
    expect(lifecycle.className).toContain("text-success");
  });

  it("prefixes lifecycle risk tones with distinct hidden icons", async () => {
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

    await sourceBom();
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

    await user.click(screen.getAllByText("Columns")[1]);
    await user.click(screen.getByRole("checkbox", { name: "Lead time" }));

    const leadTimeCellText = (rowName: RegExp) =>
      within(within(bomRowsTable).getByRole("row", { name: rowName })).getAllByRole("cell")[9].textContent;

    expect(within(bomRowsTable).getByRole("columnheader", { name: "Lead time" })).toBeDefined();
    expect(leadTimeCellText(/STM32/)).toBe("3 days");
    expect(leadTimeCellText(/Regulator/)).toBe("7 days");
    expect(leadTimeCellText(/Oscillator/)).toBe("—");
  });

});
