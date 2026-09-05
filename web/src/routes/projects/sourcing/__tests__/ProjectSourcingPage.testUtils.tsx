// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { expect, vi } from "vitest";
import { ApiError, api } from "@/lib/api";
import ProjectSourcingPage from "../ProjectSourcingPage";

export const projectId = "project-123";

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

export function sourcingResponse(overrides: Record<string, unknown> = {}) {
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
      cost_per_single_bom: "15.00",
      purchase_to_pay_cost: "25.00",
      blocking_lines_now: ["entry-2"],
      blocking_lines_after_purchase: ["entry-1"],
    },
    build_quantity: 2,
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

export function apiError(status: number, message: string, code?: string) {
  return new ApiError(
    status,
    {
      data: null,
      status: {
        category: status === 409 ? "conflict" : "server_error",
        message,
      },
      code,
    },
    message,
  );
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

export function mockReads(workspaceOverrides: Record<string, unknown> = {}) {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/workspaces/current") return workspace(workspaceOverrides) as never;
    if (path === "/workspaces/members") return [] as never;
    if (String(path).startsWith("/projects?")) return [project()] as never;
    if (path === `/projects/${projectId}`) return project() as never;
    throw new Error(`unexpected GET ${path}`);
  });
}

export function renderPage() {
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

export async function clickSource(user = userEvent.setup()) {
  const button = await screen.findByRole("button", { name: "Source" }) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false));
  await user.click(button);
  return button;
}

export async function sourceBom(user = userEvent.setup()) {
  await clickSource(user);
  expect(await screen.findByText("BOM rows")).toBeDefined();
}

/**
 * Turns on BOM-table columns that ship hidden by default.
 *
 * The BOM table defines 15 columns and shows 9; the rest sit behind
 * DataTable's Columns menu, which is what "hidden, not removed" means in
 * practice. Tests that assert on a hidden column's rendering must first
 * reach it the way a user would — which also pins that the route back is
 * still there.
 *
 * `getAllByText("Columns")[1]` is the BOM-rows table; index 0 is the
 * coverage matrix above it.
 */
export async function enableBomColumns(user: ReturnType<typeof userEvent.setup>, ...labels: string[]) {
  await user.click(screen.getAllByText("Columns")[1]);
  for (const label of labels) {
    await user.click(screen.getByRole("checkbox", { name: label }));
  }
}

export function resetProjectSourcingPageTest() {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.clearAllMocks();
}
