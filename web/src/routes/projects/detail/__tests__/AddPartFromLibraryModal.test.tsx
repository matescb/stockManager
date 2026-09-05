// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { Part, ProjectEntry } from "@/types";
import AddPartFromLibraryModal from "../AddPartFromLibraryModal";

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

const parts = [
  part({ id: "part-1", name: "STM32", mpn: "STM32F103C8T6", manufacturer: "ST", image_url: "/img/stm32.png" }),
  part({ id: "part-2", name: "Regulator", mpn: "LM1117", manufacturer: "TI" }),
];

function entry(overrides: Partial<ProjectEntry>): ProjectEntry {
  return {
    id: "entry-1",
    project_id: projectId,
    entry_type: "part",
    part_id: "part-1",
    meta_part_id: null,
    name: "STM32",
    quantity: 1,
    attrition_pct: 0,
    comments: null,
    designators: [],
    cad_footprint: null,
    cad_key: null,
    dnp: false,
    order_index: 1,
    ...overrides,
  };
}

function renderModal(onClose = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AddPartFromLibraryModal open projectId={projectId} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("AddPartFromLibraryModal", () => {
  it("renders search results from GET /parts", async () => {
    vi.spyOn(api, "get").mockResolvedValue(parts);

    renderModal();

    expect(await screen.findByText("STM32")).toBeDefined();
    expect(screen.getByText("STM32F103C8T6 - ST")).toBeDefined();
    expect(screen.getByText("Regulator")).toBeDefined();
    expect(api.get).toHaveBeenCalledWith("/parts?limit=20", expect.any(Object));
  });

  it("uses the debounced search term in the parts request", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockResolvedValue(parts);

    renderModal();
    await user.type(await screen.findByLabelText("Search library"), "STM");

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(expect.stringContaining("search=STM"), expect.any(Object));
    });
  });

  it("selecting N parts and clicking add posts N BOM rows", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockResolvedValue(parts);
    const postSpy = vi.spyOn(api, "post").mockImplementation(async (_path, body) => {
      const payload = body as { part_id: string; name: string };
      return entry({
        id: `entry-${payload.part_id}`,
        part_id: payload.part_id,
        name: payload.name,
      }) as never;
    });

    renderModal();

    await user.click(await screen.findByLabelText("Select STM32"));
    await user.click(screen.getByLabelText("Select Regulator"));
    await user.click(screen.getByRole("button", { name: "Add 2 parts to BOM" }));

    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(2));
    expect(postSpy).toHaveBeenNthCalledWith(1, `/projects/${projectId}/entries`, {
      entry_type: "part",
      part_id: "part-1",
      name: "STM32",
      quantity: 1,
      designators: [],
      dnp: false,
    });
    expect(postSpy).toHaveBeenNthCalledWith(2, `/projects/${projectId}/entries`, {
      entry_type: "part",
      part_id: "part-2",
      name: "Regulator",
      quantity: 1,
      designators: [],
      dnp: false,
    });
  });

  it("closes modal after success", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockResolvedValue(parts);
    vi.spyOn(api, "post").mockResolvedValue(entry({}) as never);
    const { onClose } = renderModal();

    await user.click(await screen.findByLabelText("Select STM32"));
    await user.click(screen.getByRole("button", { name: "Add 1 part to BOM" }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });
});
