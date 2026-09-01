// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { api } from "@/lib/api";
import { ConfirmDialogProvider } from "@/components/ConfirmDialog";
import type { PartCategory } from "@/types";
import CategoriesSettings from "../Categories";

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

const categories: PartCategory[] = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    name: "Resistors",
    description: "Fixed-value resistors",
    sort_order: 10,
    refdes_prefix: "R",
    default_symbol_ref: "Device:R",
    default_footprint_ref: "Resistor_SMD:R_0402_1005Metric",
    footprint_filters: ["R_*"],
    library_slug: "resistors",
    archived_at: null,
  },
  {
    id: "22222222-2222-4222-8222-222222222222",
    name: "Capacitors",
    description: null,
    sort_order: 20,
    refdes_prefix: "C",
    default_symbol_ref: null,
    default_footprint_ref: null,
    footprint_filters: null,
    library_slug: "capacitors",
    archived_at: "2026-08-01T10:00:00+00:00",
  },
];

function renderCategories() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ConfirmDialogProvider>
        <MemoryRouter initialEntries={["/settings/categories"]}>
          <CategoriesSettings />
        </MemoryRouter>
      </ConfirmDialogProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("CategoriesSettings", () => {
  it("renders each category with its slug, prefix and archived pill", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(categories);

    renderCategories();

    const table = await screen.findByRole("table");
    expect(within(table).getByText("Resistors")).toBeDefined();
    expect(within(table).getByText("resistors")).toBeDefined();
    expect(within(table).getByText("Device:R")).toBeDefined();
    expect(within(table).getByText("Fixed-value resistors")).toBeDefined();
    // The archived row carries the pill and a Restore action instead of Archive.
    expect(within(table).getByText("Archived")).toBeDefined();
    expect(within(table).getByRole("button", { name: "Restore" })).toBeDefined();
  });

  it("posts the form to /categories, splitting footprint filters on commas", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue([]);
    const post = vi.spyOn(api, "post").mockResolvedValue(categories[0]);

    renderCategories();

    fireEvent.click(await screen.findByRole("button", { name: "+ Category" }));
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Power MOSFETs" },
    });
    fireEvent.change(screen.getByLabelText("Reference prefix"), { target: { value: "Q" } });
    fireEvent.change(screen.getByLabelText("Sort order"), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText("Default symbol"), {
      target: { value: "Device:Q_NMOS_GDS" },
    });
    fireEvent.change(screen.getByLabelText("Footprint filters"), {
      target: { value: "SOT-23*, TO-220*" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith("/categories", {
      name: "Power MOSFETs",
      description: null,
      sort_order: 30,
      refdes_prefix: "Q",
      default_symbol_ref: "Device:Q_NMOS_GDS",
      default_footprint_ref: null,
      footprint_filters: ["SOT-23*", "TO-220*"],
    });
  });

  it("refuses to submit without a name and never calls the API", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue([]);
    const post = vi.spyOn(api, "post").mockResolvedValue(categories[0]);

    renderCategories();

    fireEvent.click(await screen.findByRole("button", { name: "+ Category" }));
    fireEvent.click(await screen.findByRole("button", { name: "Create" }));

    expect(await screen.findByText("Name is required.")).toBeDefined();
    expect(post).not.toHaveBeenCalled();
  });

  it("prefills the edit form and patches the existing category", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(categories);
    const patch = vi.spyOn(api, "patch").mockResolvedValue(categories[0]);

    renderCategories();

    const table = await screen.findByRole("table");
    const resistorsRow = within(table).getByText("Resistors").closest("tr");
    fireEvent.click(within(resistorsRow as HTMLElement).getByRole("button", { name: "Edit" }));

    const nameInput = (await screen.findByLabelText("Name")) as HTMLInputElement;
    expect(nameInput.value).toBe("Resistors");
    expect((screen.getByLabelText("Library slug") as HTMLInputElement).value).toBe("resistors");

    fireEvent.change(nameInput, { target: { value: "Resistors (SMD)" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch).toHaveBeenCalledWith(
      `/categories/${categories[0].id}`,
      expect.objectContaining({ name: "Resistors (SMD)", library_slug: "resistors" }),
    );
  });
});
