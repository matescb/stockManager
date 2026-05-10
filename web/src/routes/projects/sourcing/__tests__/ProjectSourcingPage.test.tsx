// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { ApiError, api } from "@/lib/api";
import ProjectSourcingPage from "../ProjectSourcingPage";
import { SourceBomButton } from "../SourceBomButton";

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

const projectId = "project-123";

function workspace(overrides: Record<string, unknown> = {}) {
  return {
    sourcing_country_code: "US",
    sourcing_currency_code: "USD",
    sourcing_preferred_distributors: ["DigiKey", "Mouser"],
    active_countries: ["US", "CZ", "DE"],
    active_currencies: ["USD", "EUR", "JPY"],
    active_distributors: ["DigiKey", "Mouser", "Arrow"],
    has_sourcing_company_id: true,
    has_sourcing_api_key: true,
    ...overrides,
  };
}

function project() {
  return {
    id: projectId,
    name: "Amplifier",
    description: null,
    notes_markdown: null,
    archived_at: null,
    created_at: "2026-05-08T12:00:00+00:00",
    updated_at: "2026-05-08T12:00:00+00:00",
  };
}

function sourcingResponse(overrides: Record<string, unknown> = {}) {
  return {
    rows: [
      {
        project_entry_id: "entry-1",
        part_id: "part-1",
        part_name: "STM32",
        mpn: "STM32F103C8T6",
        required: 20,
        available: 4,
        substitute_ids: [],
        substitute_available: 0,
        short_by: 16,
        authorized_stock: 60,
        offers: [
          {
            mpn: "STM32F103C8T6",
            distributor: "DigiKey",
            stock: 60,
            unit_price: "1.25",
            currency: "USD",
            moq: 1,
            lead_time_days: 3,
            url: "https://www.trustedparts.com/digikey/stm32",
          },
        ],
        best_offer: {
          mpn: "STM32F103C8T6",
          distributor: "DigiKey",
          stock: 60,
          unit_price: "1.25",
          currency: "USD",
          moq: 1,
          lead_time_days: 3,
          url: "https://www.trustedparts.com/digikey/stm32",
        },
        est_extended_cost: "20.00",
        lead_time_days: 3,
        risk_flags: ["single_source", "lead_time_long"],
      },
      {
        project_entry_id: "entry-2",
        part_id: "part-2",
        part_name: "Regulator",
        mpn: "LM1117",
        required: 10,
        available: 0,
        substitute_ids: [],
        substitute_available: 0,
        short_by: 10,
        authorized_stock: 20,
        offers: [
          {
            mpn: "LM1117",
            distributor: "Mouser",
            stock: 20,
            unit_price: "0.50",
            currency: "USD",
            moq: 1,
            lead_time_days: 7,
          },
        ],
        best_offer: {
          mpn: "LM1117",
          distributor: "Mouser",
          stock: 20,
          unit_price: "0.50",
          currency: "USD",
          moq: 1,
          lead_time_days: 7,
        },
        est_extended_cost: "5.00",
        lead_time_days: 7,
        risk_flags: ["preferred_distributor_unmet"],
      },
    ],
    coverage: {
      rows: [
        {
          distributor: "DigiKey",
          lines_covered: 1,
          lines_uncovered: ["entry-2"],
          coverage_pct: 0.5,
          est_total_cost: "20.00",
          worst_lead_time_days: 3,
        },
        {
          distributor: "Mouser",
          lines_covered: 1,
          lines_uncovered: ["entry-1"],
          coverage_pct: 0.5,
          est_total_cost: "5.00",
          worst_lead_time_days: 7,
        },
      ],
      total_lines: 2,
      best_single_distributor: "DigiKey",
      best_two_distributor_combo: ["DigiKey", "Mouser"],
      lowest_total_price_combo: ["DigiKey", "Mouser"],
      lowest_total_price_total: "25.00",
      fewest_distributors_combo: ["DigiKey", "Mouser"],
      fewest_distributors_total: "25.00",
      target_coverage_pct: 1,
    },
    capacity: {
      can_build_now: 0,
      can_build_after_purchase: 3,
      total_bom_cost: "30.00",
      purchase_to_pay_cost: "25.00",
      est_purchase_cost: "25.00",
      blocking_lines_now: ["entry-2"],
      blocking_lines_after_purchase: ["entry-1"],
    },
    powered_by: "TrustedParts" as const,
    fetched_at: "2026-05-08T12:00:00+00:00",
    partial: false,
    links: {
      primary: "https://www.trustedparts.com/",
      attribution: "https://www.trustedparts.com/en/about",
    },
    ...overrides,
  };
}

function apiError(status: number, message: string) {
  return new ApiError(
    status,
    {
      data: null,
      status: {
        category: status === 409 ? "conflict" : "server_error",
        message,
      },
    },
    message,
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function mockReads(workspaceOverrides: Record<string, unknown> = {}) {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/workspaces/current") return workspace(workspaceOverrides) as never;
    if (path === "/workspaces/members") return [] as never;
    if (String(path).startsWith("/projects?")) return [project()] as never;
    if (path === `/projects/${projectId}`) return project() as never;
    throw new Error(`unexpected GET ${path}`);
  });
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/projects/${projectId}/sourcing`]}>
        <Routes>
          <Route path="/projects/:projectId/sourcing" element={<ProjectSourcingPage />} />
          <Route path="/projects/:projectId/purchase-plans/:planId" element={<div data-testid="plan-route" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

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

    expect(await screen.findByText("Can build now")).toBeDefined();
    expect(screen.getByText("After purchase")).toBeDefined();
    expect(screen.getByText("3")).toBeDefined();
    expect(screen.getByText("Total BOM cost:")).toBeDefined();
    expect(screen.getByText("30 USD")).toBeDefined();
    expect(screen.getByText("if bought every line")).toBeDefined();
  });

  it("CapacityBanner renders Price to pay when capacity.purchase_to_pay_cost is non-null", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

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
        purchase_to_pay_cost: null,
        est_purchase_cost: null,
        blocking_lines_now: ["entry-1"],
        blocking_lines_after_purchase: ["entry-1"],
      },
    }));

    renderPage();

    expect(await screen.findByText("Total BOM cost:")).toBeDefined();
    expect(screen.getByText("no pricing available on any line")).toBeDefined();
    expect(screen.getByText("no non-blocking priced shortages")).toBeDefined();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("renders coverage matrix with best-single + best-two highlights", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    const coverage = await screen.findByText("Coverage matrix");
    expect(coverage).toBeDefined();
    const table = screen.getAllByRole("table")[0];
    expect(within(table).getByText("DigiKey")).toBeDefined();
    expect(within(table).getByText("Best single distributor")).toBeDefined();
    expect(within(table).getAllByText("Best two-distributor combo")).toHaveLength(2);
  });

  it("cold load shows the sourced BOM skeleton without the background refresh hint", async () => {
    mockReads();
    const firstLoad = deferred<ReturnType<typeof sourcingResponse>>();
    vi.spyOn(api, "post").mockReturnValue(firstLoad.promise as never);

    renderPage();

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

    expect(await screen.findByText("BOM rows")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Source" }));

    expect(await screen.findByText("Refreshing prices in the background...")).toBeDefined();
    expect(screen.getAllByText("STM32").length).toBeGreaterThan(0);
    expect(screen.queryByRole("status", { name: "Loading sourced BOM" })).toBeNull();

    refetch.resolve(sourcingResponse());
  });

  it("placeholderData renders previous result during a filter refetch", async () => {
    const user = userEvent.setup();
    mockReads();
    const filteredLoad = deferred<ReturnType<typeof sourcingResponse>>();
    const post = vi.spyOn(api, "post");
    post.mockResolvedValueOnce(sourcingResponse());
    post.mockReturnValueOnce(filteredLoad.promise as never);

    renderPage();

    expect(await screen.findByText("BOM rows")).toBeDefined();
    await user.click(screen.getByRole("checkbox", { name: "Mouser" }));

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

    expect(await screen.findByText("Lowest total price")).toBeDefined();
    expect(screen.getByText("DigiKey + Mouser")).toBeDefined();
    expect(screen.getAllByText("25 USD").length).toBeGreaterThan(0);
    expect(screen.getAllByText("100%").length).toBeGreaterThan(0);
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

    expect(await screen.findByText("Fewest distributors")).toBeDefined();
    expect(screen.getAllByText("40 USD").length).toBeGreaterThan(0);
  });

  it("When both variants are the same set, only one card renders with both labels", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    expect(await screen.findByText("Lowest total price")).toBeDefined();
    expect(screen.getByText("Fewest distributors")).toBeDefined();
    expect(screen.getAllByText("DigiKey + Mouser")).toHaveLength(1);
  });

  it("renders risk pills for each flag returned", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

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

    await screen.findByText("BOM rows");
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

    await screen.findByText("BOM rows");
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

    await screen.findByText("BOM rows");
    const bomRowsTable = screen.getAllByRole("table")[1];
    expect(within(bomRowsTable).getByRole("columnheader", { name: "Lifecycle" })).toBeDefined();
    expect(within(bomRowsTable).getByRole("columnheader", { name: "Supply chain" })).toBeDefined();
    expect(within(bomRowsTable).getByRole("columnheader", { name: "RoHS" })).toBeDefined();

    const lifecycle = screen.getByLabelText("Lifecycle risk: High");
    const supplyChain = screen.getByLabelText("Supply-chain risk: Medium");
    const tariff = screen.getByText("tariff");
    const rohs = screen.getByText("Non-compliant");
    expect(lifecycle.className).toContain("text-danger");
    expect(supplyChain.className).toContain("text-warning");
    expect(tariff.className).toContain("text-warning");
    expect(rohs.className).toContain("text-danger");

    const riskCell = within(bomRowsTable).getByRole("row", { name: /STM32/ }).querySelectorAll("td")[12];
    expect(within(riskCell as HTMLElement).queryByText("lifecycle")).toBeNull();
    expect(within(riskCell as HTMLElement).queryByText("supply chain")).toBeNull();
    expect(within(riskCell as HTMLElement).queryByText("RoHS")).toBeNull();
    expect(screen.getByLabelText("TrustedParts did not find a compliant RoHS region for this BOM line.")).toBeDefined();
  });

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

    await screen.findByText("BOM rows");
    const rohs = screen.getByText("Compliant");
    expect(rohs.className).toContain("text-success");
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

    await screen.findByText("BOM rows");
    const bomRowsTable = screen.getAllByRole("table")[1];
    expect(within(bomRowsTable).getByRole("columnheader", { name: "Lifecycle" })).toBeDefined();
    const lifecycle = screen.getByLabelText("Lifecycle risk: Obsolete");
    expect(lifecycle.className).toContain("text-danger");
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

    await screen.findByText("BOM rows");
    const lifecycle = screen.getByLabelText("Lifecycle risk: Low risk");
    expect(lifecycle.className).toContain("text-success");
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

    await screen.findByText("BOM rows");
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

  it("409 path renders not-configured card with settings link", async () => {
    mockReads();
    vi.spyOn(api, "post").mockRejectedValue(apiError(409, "sourcing not configured"));

    renderPage();

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

    expect(await screen.findByText("No matching offers found.")).toBeDefined();
    expect(screen.getByText("TrustedParts returned no authorized offers for the selected country, currency, and distributors.")).toBeDefined();
  });

  it("503 path renders budget banner", async () => {
    mockReads();
    vi.spyOn(api, "post").mockRejectedValue(apiError(503, "sourcing budget exhausted"));

    renderPage();

    expect(await screen.findByText("TrustedParts request budget reached for this hour. Retry is paused for 5 minutes.")).toBeDefined();
    const retry = screen.getByRole("button", { name: "Retry Source BOM" }) as HTMLButtonElement;
    await waitFor(() => expect(retry.disabled).toBe(true));
  });

  it("partial flag surfaces the partial badge", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({ partial: true }));

    renderPage();

    expect(await screen.findByText("Partial — some chunks served from cache")).toBeDefined();
  });

  it("BOM request body includes currency from workspace settings", async () => {
    mockReads({ sourcing_currency_code: "EUR", active_currencies: ["USD", "EUR"] });
    const post = vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    await screen.findByText("BOM rows");
    await waitFor(() => {
      expect(post).toHaveBeenCalledWith(
        `/projects/${projectId}/sourcing`,
        expect.objectContaining({
          build_quantity: 1,
          country: "US",
          currency: "EUR",
          distributors: ["DigiKey", "Mouser"],
        }),
        expect.anything(),
      );
    });
  });

  it("BOM request body sends null currency when workspace has no currency set", async () => {
    mockReads({ sourcing_currency_code: null });
    const post = vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    await screen.findByText("BOM rows");
    await waitFor(() => {
      expect(post).toHaveBeenCalledWith(
        `/projects/${projectId}/sourcing`,
        expect.objectContaining({
          build_quantity: 1,
          currency: null,
        }),
        expect.anything(),
      );
    });
  });

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

    expect(await screen.findByText("1 EUR")).toBeDefined();
    expect(screen.queryByText("2 USD")).toBeNull();
    expect(screen.getByText("20 USD")).toBeDefined();
  });

  it("Generate purchase plan posts default strategy from current sourcing filters", async () => {
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

    await screen.findByText("BOM rows");
    await userEvent.click(screen.getByRole("button", { name: "Generate purchase plan" }));
    await userEvent.click(screen.getByRole("button", { name: "Generate" }));

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

    await screen.findByText("BOM rows");
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

  it("502 path shows toast and retry action", async () => {
    mockReads();
    vi.spyOn(api, "post").mockRejectedValue(apiError(502, "TrustedParts request timed out"));

    renderPage();

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        "TrustedParts unavailable. Retry?",
        expect.objectContaining({
          action: expect.objectContaining({ label: "Retry" }),
        }),
      );
    });
    expect(screen.getByRole("button", { name: "Retry Source BOM" })).toBeDefined();
  });
});
