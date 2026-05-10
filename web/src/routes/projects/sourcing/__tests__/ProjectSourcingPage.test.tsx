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
    },
    capacity: {
      can_build_now: 0,
      can_build_after_purchase: 3,
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

  it("renders capacity banner with both numbers from server response", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    expect(await screen.findByText("Can build now")).toBeDefined();
    expect(screen.getByText("After purchase")).toBeDefined();
    expect(screen.getByText("3")).toBeDefined();
    expect(screen.getAllByText("Est. cost").length).toBeGreaterThan(0);
    expect(screen.getByText("25 USD")).toBeDefined();
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

  it("renders risk pills for each flag returned", async () => {
    mockReads();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse());

    renderPage();

    expect(await screen.findByText("Single source")).toBeDefined();
    expect(screen.getByText("Long lead time")).toBeDefined();
    expect(screen.getByText("Preferred unmet")).toBeDefined();
    expect(screen.getAllByLabelText("Source: TrustedParts").length).toBeGreaterThan(0);
  });

  it("renders new risk pills with tooltip text and colours", async () => {
    mockReads();
    const base = sourcingResponse();
    vi.spyOn(api, "post").mockResolvedValue(sourcingResponse({
      rows: [
        {
          ...base.rows[0],
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

    expect(await screen.findByText("lifecycle")).toBeDefined();
    expect(screen.getByText("supply chain")).toBeDefined();
    const tariff = screen.getByText("tariff");
    const rohs = screen.getByText("RoHS");
    expect(tariff.className).toContain("text-warning");
    expect(rohs.className).toContain("text-danger");
    expect(screen.getByLabelText("TrustedParts returned lifecycle risk text for this BOM line.")).toBeDefined();
    expect(screen.getByLabelText("TrustedParts did not find a compliant RoHS region for this BOM line.")).toBeDefined();
  });

  it("lifecycle column is hidden by default and renders Obsolete as red when enabled", async () => {
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

    await screen.findByText("BOM rows");
    const bomRowsTable = screen.getAllByRole("table")[1];
    expect(within(bomRowsTable).queryByRole("columnheader", { name: "Lifecycle" })).toBeNull();

    await user.click(screen.getAllByText("Columns")[1]);
    await user.click(screen.getByRole("checkbox", { name: "Lifecycle" }));

    expect(within(bomRowsTable).getByRole("columnheader", { name: "Lifecycle" })).toBeDefined();
    const lifecycle = screen.getByLabelText("Lifecycle risk: Obsolete");
    expect(lifecycle.className).toContain("text-danger");
  });

  it("renders per-row lead time values in the BOM rows table", async () => {
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
