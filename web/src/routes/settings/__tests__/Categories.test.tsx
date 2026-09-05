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
    parent_id: null,
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
    parent_id: "11111111-1111-4111-8111-111111111111",
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
      parent_id: null,
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

describe("CategoriesSettings — hierarchy", () => {
  const tree: PartCategory[] = [
    { ...categories[0], id: "cat-passives", name: "Passives", parent_id: null },
    {
      ...categories[0],
      id: "cat-resistors",
      name: "Resistors",
      parent_id: "cat-passives",
    },
    {
      ...categories[0],
      id: "cat-thin-film",
      name: "Thin film",
      parent_id: "cat-resistors",
    },
    { ...categories[0], id: "cat-actives", name: "Actives", parent_id: null },
  ];

  function rowFor(name: string): HTMLElement {
    const table = screen.getByRole("table");
    return within(table).getByText(name).closest("tr") as HTMLElement;
  }

  it("renders children under their parent, depth-first", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(tree);
    renderCategories();

    const table = await screen.findByRole("table");
    const order = within(table)
      .getAllByRole("row")
      .slice(1) // header
      .map((tr) => (tr.textContent ?? "").trim());
    expect(order[0]).toMatch(/^Actives/);
    expect(order[1]).toMatch(/Passives/);
    expect(order[2]).toMatch(/Resistors/);
    expect(order[3]).toMatch(/Thin film/);
  });

  it("offers a parent picker with full paths, and posts the chosen parent", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(tree);
    const post = vi.spyOn(api, "post").mockResolvedValue(tree[0]);

    renderCategories();
    fireEvent.click(await screen.findByRole("button", { name: "+ Category" }));

    const picker = (await screen.findByLabelText(
      "Parent category",
    )) as HTMLSelectElement;
    const labels = [...picker.options].map((o) => o.textContent);
    expect(labels).toContain("Passives / Resistors");
    expect(labels).toContain("Passives / Resistors / Thin film");

    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Thick film" },
    });
    fireEvent.change(picker, { target: { value: "cat-resistors" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith(
      "/categories",
      expect.objectContaining({ name: "Thick film", parent_id: "cat-resistors" }),
    );
  });

  it("excludes the category and its descendants from its own parent picker", async () => {
    // Offering them would just earn a `category.parent_cycle` 422 — the
    // server refuses either move.
    vi.spyOn(api.parsed, "get").mockResolvedValue(tree);
    renderCategories();

    await screen.findByRole("table");
    fireEvent.click(within(rowFor("Passives")).getByRole("button", { name: "Edit" }));

    const picker = (await screen.findByLabelText(
      "Parent category",
    )) as HTMLSelectElement;
    const values = [...picker.options].map((o) => o.value);
    expect(values).not.toContain("cat-passives");
    expect(values).not.toContain("cat-resistors");
    expect(values).not.toContain("cat-thin-film");
    expect(values).toContain("cat-actives");
  });

  it("prefills the picker with the stored parent and can clear it to root", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(tree);
    const patch = vi.spyOn(api, "patch").mockResolvedValue(tree[1]);

    renderCategories();
    await screen.findByRole("table");
    fireEvent.click(within(rowFor("Resistors")).getByRole("button", { name: "Edit" }));

    const picker = (await screen.findByLabelText(
      "Parent category",
    )) as HTMLSelectElement;
    expect(picker.value).toBe("cat-passives");

    fireEvent.change(picker, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch).toHaveBeenCalledWith(
      "/categories/cat-resistors",
      expect.objectContaining({ parent_id: null }),
    );
  });

  it("warns that archiving promotes subcategories to the top level, and names them", async () => {
    // `ON DELETE SET NULL` does not cascade. The subtree survives and moves
    // up — safe, but not what "archive" suggests, so the dialog says it.
    vi.spyOn(api.parsed, "get").mockResolvedValue(tree);
    const post = vi.spyOn(api, "post").mockResolvedValue({});

    renderCategories();
    await screen.findByRole("table");
    fireEvent.click(
      within(rowFor("Passives")).getByRole("button", { name: "Archive" }),
    );

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/move.* up to the top level/i)).toBeDefined();
    expect(within(dialog).getByText(/Resistors/)).toBeDefined();
    // The grandchild is NOT promoted — only direct children are.
    expect(within(dialog).queryByText(/Thin film/)).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("says nothing about promotion when the category has no children", async () => {
    vi.spyOn(api.parsed, "get").mockResolvedValue(tree);
    renderCategories();
    await screen.findByRole("table");
    fireEvent.click(
      within(rowFor("Actives")).getByRole("button", { name: "Archive" }),
    );

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText(/top level/i)).toBeNull();
  });
});
