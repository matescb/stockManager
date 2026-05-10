// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { Part, ProjectEntry } from "@/types";
import ProjectBOM from "../ProjectBOM";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

const projectId = "project-1";

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function part(overrides: Partial<Part>): Part {
  return {
    id: "part-1",
    part_type: "local",
    name: "STM32",
    manufacturer: "ST",
    mpn: "STM32F103C8T6",
    internal_part_number: null,
    description: null,
    footprint: null,
    notes_markdown: null,
    low_stock_report_quantity: null,
    attrition_percentage: 0,
    attrition_min_quantity: 0,
    default_storage_location_id: null,
    default_storage_mandatory: false,
    serialized: false,
    published: false,
    linked_provider: null,
    linked_external_id: null,
    last_refresh_at: null,
    description_locally_edited: false,
    archived_at: null,
    on_hand: 0,
    reserved: 0,
    available: 0,
    image_url: null,
    ...overrides,
  };
}

function bomEntry(overrides: Partial<ProjectEntry>): ProjectEntry {
  return {
    id: "entry-1",
    project_id: projectId,
    entry_type: "part",
    part_id: "part-1",
    meta_part_id: null,
    name: "STM32",
    quantity: 2,
    comments: null,
    designators: ["U1"],
    cad_footprint: null,
    cad_key: null,
    dnp: false,
    order_index: 1,
    ...overrides,
  };
}

const parts = [
  part({ id: "part-1", name: "STM32", mpn: "STM32F103C8T6", manufacturer: "ST", image_url: "/img/stm32.png" }),
  part({ id: "part-2", name: "Regulator", mpn: "LM1117", manufacturer: "TI" }),
];

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/projects/${projectId}/bom`]}>
        <Routes>
          <Route path="/projects/:projectId/bom" element={<><LocationProbe /><ProjectBOM /></>} />
          <Route path="/projects/:projectId/import" element={<LocationProbe />} />
          <Route path="/parts/:partId/info" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockApi(entriesRef: { current: ProjectEntry[] }) {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === `/projects/${projectId}/entries`) return entriesRef.current as never;
    if (path === "/parts?limit=200") return parts as never;
    if (path === "/workspaces/current") {
      return {
        sourcing_country_code: "US",
        sourcing_currency_code: "USD",
        sourcing_preferred_distributors: [],
        has_sourcing_company_id: true,
      } as never;
    }
    throw new Error(`unexpected GET ${path}`);
  });
  vi.spyOn(api, "delete").mockImplementation(async path => {
    const id = String(path).split("/").pop();
    entriesRef.current = entriesRef.current.filter(entry => entry.id !== id);
    return null as never;
  });
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("ProjectBOM", () => {
  it("renders three action buttons in the toolbar", async () => {
    mockApi({ current: [] });

    renderPage();

    expect(await screen.findByRole("link", { name: "Source BOM" })).toBeDefined();
    expect(screen.getByRole("link", { name: "Import BOM" }).getAttribute("href")).toBe(`/projects/${projectId}/import`);
    expect(screen.getByRole("button", { name: "Add Part" })).toBeDefined();
  });

  it("matched-row click navigates to part info", async () => {
    mockApi({ current: [bomEntry({})] });
    const user = userEvent.setup();

    renderPage();

    await user.click(await screen.findByText("STM32"));
    await waitFor(() => {
      expect(screen.getByTestId("location").textContent).toBe("/parts/part-1/info");
    });
  });

  it("unmatched-row click does nothing", async () => {
    mockApi({
      current: [
        bomEntry({
          id: "entry-unmatched",
          entry_type: "unmatched",
          part_id: null,
          name: "Mystery line",
        }),
      ],
    });
    const user = userEvent.setup();

    renderPage();

    await user.click(await screen.findByText("Mystery line"));
    expect(screen.getByTestId("location").textContent).toBe(`/projects/${projectId}/bom`);
  });

  it("multi-select + bulk-delete removes selected rows", async () => {
    const entriesRef = {
      current: [
        bomEntry({ id: "entry-1", part_id: "part-1", name: "STM32" }),
        bomEntry({ id: "entry-2", part_id: "part-2", name: "Regulator" }),
      ],
    };
    mockApi(entriesRef);
    const user = userEvent.setup();

    renderPage();

    expect(await screen.findByText("STM32")).toBeDefined();
    const rowCheckboxes = screen
      .getAllByRole("checkbox")
      .filter(input => input.getAttribute("aria-label") === "Select row");
    await user.click(rowCheckboxes[0]);
    await user.click(rowCheckboxes[1]);
    await user.click(screen.getByRole("button", { name: "Delete (2)" }));

    await waitFor(() => {
      expect(api.delete).toHaveBeenCalledWith(`/projects/${projectId}/entries/entry-1`);
      expect(api.delete).toHaveBeenCalledWith(`/projects/${projectId}/entries/entry-2`);
    });
    await waitFor(() => {
      expect(screen.queryByText("STM32")).toBeNull();
      expect(screen.queryByText("Regulator")).toBeNull();
    });
  });
});
