/**
 * Printable pick list (Track B4) — DOM behaviour.
 *
 * What matters on a sheet an operator carries to the shelves:
 *
 * * the walk is rendered stop by stop, in server order, with a per-location
 *   quantity — not one total per part;
 * * every quantity carries its unit;
 * * shortfalls are visible, including a line with no stock anywhere (which
 *   has no stop at all and would otherwise vanish);
 * * the print stylesheet actually ships and hides the app chrome;
 * * the stage picker navigates to the per-stage endpoint, and the on-screen
 *   controls are marked no-print.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/instrument", () => ({}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

vi.mock("@/lib/queryKeys", () => ({
  useWsKey: (...args: unknown[]) => ["ws-1", ...args],
  wsKeyOf: (...args: unknown[]) => args,
}));

const requested: string[] = [];

const WHOLE_BUILD = {
  build: { id: "b1", name: "Rev C run", quantity: 5, status: "planned" },
  project: { id: "p1", name: "Widget" },
  stage: null,
  generated_at: "2026-09-05T10:00:00+00:00",
  lines: [
    {
      project_entry_id: "e1",
      part_id: "part-r",
      part_name: "R10k",
      mpn: "RC0603-10K",
      manufacturer: "Yageo",
      internal_part_number: null,
      designators: ["R1", "R2"],
      unit: "pcs",
      attrition_pct: 25,
      portion_pct: null,
      required: 138,
      on_hand: 180,
      alternates_available: 0,
      planned: 138,
      short_by: 0,
      is_short: false,
      location_count: 2,
    },
    {
      project_entry_id: "e2",
      part_id: "part-x",
      part_name: "XTAL 16M",
      mpn: null,
      manufacturer: null,
      internal_part_number: null,
      designators: ["X1"],
      unit: "pcs",
      attrition_pct: 0,
      portion_pct: null,
      required: 5,
      on_hand: 0,
      alternates_available: 20,
      planned: 0,
      short_by: 5,
      is_short: true,
      location_count: 0,
    },
  ],
  stops: [
    {
      storage_location_id: "s-a1",
      storage_location_name: "A1 shelf",
      picks: [
        {
          project_entry_id: "e1",
          part_id: "part-r",
          part_name: "R10k",
          mpn: "RC0603-10K",
          designators: ["R1", "R2"],
          lot_id: null,
          lot_name: null,
          quantity: 100,
          unit: "pcs",
          available: 100,
        },
      ],
    },
    {
      storage_location_id: null,
      storage_location_name: "Unassigned",
      picks: [
        {
          project_entry_id: "e1",
          part_id: "part-r",
          part_name: "R10k",
          mpn: "RC0603-10K",
          designators: ["R1", "R2"],
          lot_id: null,
          lot_name: null,
          quantity: 38,
          unit: "pcs",
          available: 80,
        },
      ],
    },
  ],
  totals: { lines: 2, short_lines: 1, stops: 2 },
};

const STAGE_SHEET = {
  ...WHOLE_BUILD,
  stage: { id: "st-1", name: "SMT reflow", sequence: 0, status: "planned" },
  lines: [{ ...WHOLE_BUILD.lines[0], required: 69, planned: 69, portion_pct: 50 }],
  stops: [
    {
      ...WHOLE_BUILD.stops[0],
      picks: [{ ...WHOLE_BUILD.stops[0].picks[0], quantity: 69 }],
    },
  ],
  totals: { lines: 1, short_lines: 0, stops: 1 },
};

const STAGES = [
  { id: "st-1", name: "SMT reflow", sequence: 0, status: "planned" },
  { id: "st-2", name: "THT", sequence: 1, status: "planned" },
];

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    body: unknown;
    userMessage: string;
    constructor(status: number, body: unknown, msg = "api error") {
      super(msg);
      this.status = status;
      this.body = body;
      this.userMessage = msg;
    }
  }
  return {
    ApiError,
    api: {
      get: vi.fn((url: string) => {
        requested.push(url);
        if (url.endsWith("/stages")) return Promise.resolve(STAGES);
        if (url.includes("/stages/")) return Promise.resolve(STAGE_SHEET);
        if (url.endsWith("/pick-list")) return Promise.resolve(WHOLE_BUILD);
        return Promise.resolve(null);
      }),
      post: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
      upload: vi.fn(),
    },
  };
});

async function mount(initial = "/builds/b1/pick-list") {
  const { default: PickListView } = await import("../picklist/PickListView");
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/builds/:buildId/pick-list" element={<PickListView />} />
          <Route
            path="/builds/:buildId/stages/:stageId/pick-list"
            element={<PickListView />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** ["take", "at location"] from a stop block's single pick row. */
function takeCells(stop: Element): string[] {
  const cells = stop.querySelectorAll("tbody td");
  return [cells[4].textContent ?? "", cells[5].textContent ?? ""];
}

/** Cell texts of the summary row for `partName` (whole-build column order:
 *  part, designators, required, on hand, picked, short, locations). */
function summaryRow(container: HTMLElement, partName: string): string[] {
  const summary = container.querySelector(".picklist-summary") as HTMLElement;
  const row = within(summary).getByText(partName).closest("tr") as HTMLElement;
  return [...row.querySelectorAll("td")].map(td => td.textContent ?? "");
}

beforeEach(() => {
  vi.resetModules();
  requested.length = 0;
});

afterEach(() => {
  cleanup();
});

describe("PickListView — printable sheet", () => {
  it("renders the walk stop by stop with per-location quantities and units", async () => {
    const { container } = await mount();
    await waitFor(() => {
      expect(screen.getByText(/Pick list — Rev C run/)).toBeTruthy();
    });

    const stops = container.querySelectorAll(".picklist-stop");
    expect(stops.length).toBe(2);
    // Server order is the walk order — named shelf first, unassigned last.
    expect(stops[0].textContent).toContain("1. A1 shelf");
    expect(stops[1].textContent).toContain("2. Unassigned");

    // Per-location quantity in the "Take" column, not the 138 total, and
    // always with the unit. Column 4 is Take, column 5 is what the bin holds.
    expect(takeCells(stops[0])).toEqual(["100 pcs", "100 pcs"]);
    expect(takeCells(stops[1])).toEqual(["38 pcs", "80 pcs"]);
  });

  it("flags shortfalls, including a line with no stock and therefore no stop", async () => {
    const { container } = await mount();
    await waitFor(() => {
      expect(screen.getByText(/1 line is short/)).toBeTruthy();
    });

    // XTAL 16M appears nowhere in the route (nothing to fetch) but must not
    // fall off the sheet — it is in the summary, marked short.
    const route = container.querySelector(".picklist-section") as HTMLElement;
    expect(route.textContent).not.toContain("XTAL 16M");

    // part, designators, required, on hand, picked, short, locations.
    // The short cell also names the substitute stock, so the sheet never
    // reads as a blocker for a build the build screen calls covered.
    expect(summaryRow(container, "XTAL 16M")).toEqual([
      "XTAL 16M",
      "X1",
      "5 pcs",
      "0 pcs",
      "0 pcs",
      "5 pcs20 pcs in substitutes — decide at consume",
      "0",
    ]);
    const summary = container.querySelector(".picklist-summary") as HTMLElement;
    expect(within(summary).getByText(/^5 pcs/, { selector: ".picklist-short" })).toBeTruthy();
  });

  it("shows the attrition-adjusted required quantity the server computed", async () => {
    const { container } = await mount();
    await waitFor(() => {
      expect(screen.getByText(/Pick list — Rev C run/)).toBeTruthy();
    });
    // 138, not the un-inflated 5 x 20 the BOM would suggest — the sheet
    // renders `required` verbatim and never recomputes it.
    expect(summaryRow(container, "R10k")).toEqual([
      "R10kRC0603-10K",
      "R1, R2",
      "138 pcs",
      "180 pcs",
      "138 pcs",
      "—",
      "2",
    ]);
  });

  it("ships a print stylesheet that hides the app chrome and paginates stops", async () => {
    const { container } = await mount();
    await waitFor(() => {
      expect(screen.getByText(/Pick list — Rev C run/)).toBeTruthy();
    });

    expect(container.querySelector("[data-picklist-root]")).toBeTruthy();
    const css = container.querySelector("style")?.textContent ?? "";
    expect(css).toContain("@media print");
    expect(css).toContain("@page");
    // Chrome goes invisible, the sheet comes back.
    expect(css).toContain("body * { visibility: hidden; }");
    expect(css).toContain("[data-picklist-root], [data-picklist-root] * { visibility: visible; }");
    // On-screen controls never reach the paper.
    expect(css).toContain(".picklist-noprint { display: none !important; }");
    // A shelf is not split across a page break.
    expect(css).toContain(".picklist-stop { break-inside: avoid");
    expect(css).toContain("thead { display: table-header-group; }");
  });

  it("keeps the controls out of print and offers the browser print dialog", async () => {
    const printSpy = vi.fn();
    Object.defineProperty(window, "print", { value: printSpy, writable: true });

    const { container } = await mount();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Print" })).toBeTruthy();
    });

    const controls = container.querySelector(".picklist-noprint") as HTMLElement;
    expect(within(controls).getByRole("button", { name: "Print" })).toBeTruthy();
    expect(within(controls).getByRole("link", { name: /Back to build/ })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Print" }));
    expect(printSpy).toHaveBeenCalledTimes(1);
  });

  it("switches to the per-stage endpoint from the stage picker", async () => {
    await mount();
    await waitFor(() => {
      expect(screen.getByLabelText("Stage")).toBeTruthy();
    });
    expect(requested).toContain("/builds/b1/pick-list");

    fireEvent.change(screen.getByLabelText("Stage"), { target: { value: "st-1" } });

    await waitFor(() => {
      expect(requested).toContain("/builds/b1/stages/st-1/pick-list");
    });
    await waitFor(() => {
      expect(screen.getByText(/Pick list — Rev C run · SMT reflow/)).toBeTruthy();
    });
    // The stage sheet says so, and shows the stage's share of the line.
    expect(screen.getByText(/This stage only/)).toBeTruthy();
    expect(screen.getByText("50%")).toBeTruthy();
  });

  it("hides the stage picker on a single-pass build", async () => {
    const { api } = await import("@/lib/api");
    (api.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      requested.push(url);
      if (url.endsWith("/stages")) return Promise.resolve([]);
      return Promise.resolve(WHOLE_BUILD);
    });

    await mount();
    await waitFor(() => {
      expect(screen.getByText(/Pick list — Rev C run/)).toBeTruthy();
    });
    expect(screen.queryByLabelText("Stage")).toBeNull();
  });
});
