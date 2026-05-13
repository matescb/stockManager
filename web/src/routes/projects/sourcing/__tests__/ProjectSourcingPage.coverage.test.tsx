// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { clickSource, deferred, mockReads, renderPage, resetProjectSourcingPageTest, sourceBom, sourcingResponse } from "./ProjectSourcingPage.testUtils";

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
  it("renders coverage matrix with best-single + best-two highlights", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();
    await clickSource();

    const coverage = await screen.findByText("Coverage matrix");
    expect(coverage).toBeDefined();
    const table = screen.getAllByRole("table")[0];
    expect(within(table).getByText("DigiKey")).toBeDefined();
    expect(within(table).getByText("Best single distributor")).toBeDefined();
    expect(within(table).getAllByText("Best two-distributor combo")).toHaveLength(2);
  });

  it("first submit shows the sourced BOM skeleton without the background refresh hint", async () => {
    mockReads();
    const firstLoad = deferred<ReturnType<typeof sourcingResponse>>();
    vi.spyOn(api, "post").mockReturnValue(firstLoad.promise as never);

    renderPage();
    await clickSource();

    expect(await screen.findByRole("status", { name: "Loading sourced BOM" })).toBeDefined();
    expect(screen.queryByText("Refreshing prices in the background...")).toBeNull();

    firstLoad.resolve(sourcingResponse());
  });

  it("refetching state shows a muted refresh hint while keeping loaded rows visible", async () => {
    const user = userEvent.setup();
    mockReads();
    const refetch = deferred<ReturnType<typeof sourcingResponse>>();
    const post = vi.spyOn(api, "post");
    post.mockResolvedValueOnce(sourcingResponse());
    post.mockReturnValueOnce(refetch.promise as never);

    renderPage();

    await sourceBom(user);
    await clickSource(user);

    expect(await screen.findByText("Refreshing prices in the background...")).toBeDefined();
    expect(screen.getAllByText("STM32").length).toBeGreaterThan(0);
    expect(screen.queryByRole("status", { name: "Loading sourced BOM" })).toBeNull();

    refetch.resolve(sourcingResponse());
  });

  it("display cache keeps previous result until a filter refresh is submitted", async () => {
    const user = userEvent.setup();
    mockReads();
    const filteredLoad = deferred<ReturnType<typeof sourcingResponse>>();
    const post = vi.spyOn(api, "post");
    post.mockResolvedValueOnce(sourcingResponse());
    post.mockReturnValueOnce(filteredLoad.promise as never);

    renderPage();

    await sourceBom(user);
    await user.click(screen.getByRole("checkbox", { name: "Mouser" }));

    expect(post).toHaveBeenCalledTimes(1);
    await clickSource(user);
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Refreshing prices in the background...")).toBeDefined();
    expect(screen.getAllByText("STM32").length).toBeGreaterThan(0);
    expect(screen.getByText("Regulator")).toBeDefined();
    expect(screen.queryByRole("status", { name: "Loading sourced BOM" })).toBeNull();

    filteredLoad.resolve(sourcingResponse());
  });

  it("Coverage card renders Lowest total price variant with distributor names + total", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      coverage: {
        ...sourcingResponse().coverage,
        rows: [
          ...sourcingResponse().coverage.rows,
          {
            distributor: "Arrow",
            lines_covered: 2,
            lines_uncovered: [],
            coverage_pct: 1,
            est_total_cost: "40.00",
            worst_lead_time_days: 5,
          },
        ],
        lowest_total_price_combo: ["DigiKey", "Mouser"],
        lowest_total_price_total: "25.00",
        fewest_distributors_combo: ["Arrow"],
        fewest_distributors_total: "40.00",
      },
    }));

    renderPage();
    await clickSource();

    expect(await screen.findByText("Lowest total price")).toBeDefined();
    expect(screen.getByText("DigiKey + Mouser")).toBeDefined();
    expect(screen.getAllByText("Price").length).toBeGreaterThan(0);
    expect(screen.getAllByText("25 USD").length).toBeGreaterThan(0);
    expect(screen.getAllByText("100%").length).toBeGreaterThan(0);
  });

  it("floors displayed coverage percentages instead of rounding up to 100%", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      rows: [
        {
          ...sourcingResponse().rows[0],
          project_entry_id: "entry-1",
        },
        {
          ...sourcingResponse().rows[1],
          project_entry_id: "entry-2",
        },
        {
          ...sourcingResponse().rows[1],
          project_entry_id: "entry-3",
          part_id: "part-3",
          part_name: "Oscillator",
          mpn: "ABCD1234",
        },
        {
          ...sourcingResponse().rows[1],
          project_entry_id: "entry-4",
          part_id: "part-4",
          part_name: "Connector",
          mpn: "CONN-4",
        },
        {
          ...sourcingResponse().rows[1],
          project_entry_id: "entry-5",
          part_id: "part-5",
          part_name: "Ferrite",
          mpn: "FB-5",
        },
      ],
      coverage: {
        ...sourcingResponse().coverage,
        total_lines: 5,
        rows: [
          {
            distributor: "DigiKey",
            lines_covered: 199,
            lines_uncovered: ["entry-5"],
            coverage_pct: 0.995,
            est_total_cost: "20.00",
            worst_lead_time_days: 3,
          },
        ],
        best_single_distributor: "DigiKey",
        best_two_distributor_combo: ["DigiKey"],
        lowest_total_price_combo: ["DigiKey"],
        lowest_total_price_total: "20.00",
        fewest_distributors_combo: ["DigiKey"],
        fewest_distributors_total: "20.00",
      },
    }));

    renderPage();
    await clickSource();

    await screen.findByText("Coverage matrix");
    const table = screen.getAllByRole("table")[0];
    expect(within(table).getByText("99%")).toBeDefined();
    expect(within(table).queryByText("100%")).toBeNull();
  });

  it("Coverage card labels partial variant totals as covered-line prices", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      coverage: {
        ...sourcingResponse().coverage,
        lowest_total_price_combo: ["DigiKey"],
        lowest_total_price_total: "20.00",
        fewest_distributors_combo: ["DigiKey"],
        fewest_distributors_total: "20.00",
      },
    }));

    renderPage();
    await clickSource();

    expect(await screen.findByText("Price (covered lines)")).toBeDefined();
    expect(screen.getByText("1 uncovered line")).toBeDefined();
    expect(screen.getAllByText("20 USD").length).toBeGreaterThan(0);
  });

  it("Coverage card explains null covered-line totals", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      coverage: {
        ...sourcingResponse().coverage,
        lowest_total_price_combo: ["DigiKey"],
        lowest_total_price_total: null,
        fewest_distributors_combo: ["DigiKey"],
        fewest_distributors_total: null,
      },
    }));

    renderPage();
    await clickSource();

    expect(await screen.findByText("Price (covered lines)")).toBeDefined();
    expect(screen.getByText("No pricing available on covered lines.")).toBeDefined();
  });

  it("Coverage card renders Fewest distributors variant", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      coverage: {
        ...sourcingResponse().coverage,
        rows: [
          ...sourcingResponse().coverage.rows,
          {
            distributor: "Arrow",
            lines_covered: 2,
            lines_uncovered: [],
            coverage_pct: 1,
            est_total_cost: "40.00",
            worst_lead_time_days: 5,
          },
        ],
        lowest_total_price_combo: ["DigiKey", "Mouser"],
        lowest_total_price_total: "25.00",
        fewest_distributors_combo: ["Arrow"],
        fewest_distributors_total: "40.00",
      },
    }));

    renderPage();
    await clickSource();

    expect(await screen.findByText("Fewest distributors")).toBeDefined();
    expect(screen.getAllByText("40 USD").length).toBeGreaterThan(0);
  });

  it("When both variants are the same set, only one card renders with both labels", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();
    await clickSource();

    expect(await screen.findByText("Lowest total price")).toBeDefined();
    expect(screen.getByText("Fewest distributors")).toBeDefined();
    expect(screen.getAllByText("DigiKey + Mouser")).toHaveLength(1);
  });

});
