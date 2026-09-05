// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { Part, Project } from "@/types";
import ReplaceInProjectsModal from "../ReplaceInProjectsModal";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

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

function project(overrides: Partial<Project>): Project {
  return {
    id: "project-1",
    name: "Alpha",
    description: null,
    notes_markdown: null,
    associated_subassembly_part_id: null,
    archived_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

const source = part({ id: "part-1", name: "STM32" });
const parts = [
  source,
  part({ id: "part-2", name: "Regulator", mpn: "LM1117", manufacturer: "TI" }),
];
const projects = [
  project({ id: "project-1", name: "Alpha" }),
  project({ id: "project-2", name: "Beta" }),
];

function mockGet() {
  return vi.spyOn(api, "get").mockImplementation((path: string) => {
    if (path.startsWith("/projects")) return Promise.resolve(projects as never);
    return Promise.resolve(parts as never);
  });
}

function renderModal(onClose = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ReplaceInProjectsModal open part={source} onClose={onClose} />
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

describe("ReplaceInProjectsModal", () => {
  it("lists candidate parts but excludes the source part", async () => {
    mockGet();
    renderModal();

    expect(await screen.findByText("Regulator")).toBeDefined();
    // The source part must not be offered as its own replacement.
    expect(screen.queryByLabelText("Use STM32 as replacement")).toBeNull();
  });

  it("replaces across all projects by default (no project_ids)", async () => {
    const user = userEvent.setup();
    mockGet();
    const postSpy = vi
      .spyOn(api, "post")
      .mockResolvedValue({ updated_entries: 3, affected_projects: 2 } as never);
    const { onClose } = renderModal();

    await user.click(await screen.findByLabelText("Use Regulator as replacement"));
    await user.click(screen.getByRole("button", { name: "Replace part" }));

    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(1));
    expect(postSpy).toHaveBeenCalledWith("/parts/part-1/replace-in-projects", {
      target_part_id: "part-2",
    });
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("scopes to explicitly selected projects", async () => {
    const user = userEvent.setup();
    mockGet();
    const postSpy = vi
      .spyOn(api, "post")
      .mockResolvedValue({ updated_entries: 1, affected_projects: 1 } as never);
    renderModal();

    await user.click(await screen.findByLabelText("Use Regulator as replacement"));
    await user.click(screen.getByLabelText("Replace across all projects"));
    await user.click(await screen.findByLabelText("Include project Beta"));
    await user.click(screen.getByRole("button", { name: "Replace part" }));

    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(1));
    expect(postSpy).toHaveBeenCalledWith("/parts/part-1/replace-in-projects", {
      target_part_id: "part-2",
      project_ids: ["project-2"],
    });
  });

  it("blocks submit until a replacement part is chosen", async () => {
    mockGet();
    renderModal();

    // Nothing selected yet → the submit button is disabled.
    const submit = await screen.findByRole("button", { name: "Replace part" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
  });
});
