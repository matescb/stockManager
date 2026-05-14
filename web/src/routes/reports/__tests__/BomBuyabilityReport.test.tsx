// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import BomBuyabilityReport from "../BomBuyabilityReport";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

function report(overrides: Record<string, unknown> = {}) {
  return {
    build_quantity: 2,
    sourcing_status: "ok",
    truncated: false,
    project_cap: 50,
    powered_by: "TrustedParts",
    links: {
      primary: "https://www.trustedparts.com/",
      attribution: "https://www.trustedparts.com/en/about",
    },
    rows: [
      {
        project_id: "project-1",
        project_name: "Amplifier",
        build_quantity: 2,
        can_build_now: 1,
        can_build_after_purchase: 2,
        blocking_lines_count: 0,
        est_purchase_cost: "12.50",
        partial: false,
      },
      {
        project_id: "project-2",
        project_name: "Controller",
        build_quantity: 2,
        can_build_now: 0,
        can_build_after_purchase: 1,
        blocking_lines_count: 3,
        est_purchase_cost: null,
        partial: true,
      },
    ],
    ...overrides,
  };
}

function renderReport(initialPath = "/reports/buyability") {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/reports/buyability" element={<BomBuyabilityReport />} />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}{location.search}</div>;
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("BomBuyabilityReport", () => {
  it("renders rows from server response", async () => {
    vi.spyOn(api, "get").mockResolvedValue(report());

    renderReport();

    expect(await screen.findByText("Amplifier")).toBeDefined();
    expect(screen.getByText("Controller")).toBeDefined();
    expect(screen.getByText("Sourcing ok")).toBeDefined();
    expect(screen.getByText("Powered by TrustedParts")).toBeDefined();
    const amplifier = screen.getByRole("link", { name: "Amplifier" });
    expect(amplifier.getAttribute("href")).toBe("/projects/project-1/sourcing");
    const links = screen.getAllByRole("link", { name: "Open" });
    expect(links[0].getAttribute("href")).toBe("/projects/project-1/sourcing?build_quantity=2");
  });

  it("truncated badge visible when truncated=true", async () => {
    vi.spyOn(api, "get").mockResolvedValue(report({ truncated: true }));

    renderReport();

    expect(await screen.findByText("Truncated to 50 projects")).toBeDefined();
  });

  it("build quantity input updates URL and refetches", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(report());
    const user = userEvent.setup();

    renderReport();

    await screen.findByText("Amplifier");
    const input = screen.getByLabelText("Build quantity");
    await user.clear(input);
    await user.type(input, "5{Enter}");

    await waitFor(() => {
      expect(get).toHaveBeenCalledWith(
        "/reports/bom-buyability?build_quantity=5",
        expect.any(Object),
      );
    });
    expect(screen.getByTestId("location").textContent).toContain("build_quantity=5");
    const table = screen.getByRole("table");
    expect(within(table).getByText("Amplifier")).toBeDefined();
  });
});
