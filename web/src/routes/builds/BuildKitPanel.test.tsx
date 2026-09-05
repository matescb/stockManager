// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/lib/api";
import BuildKitPanel from "./BuildKitPanel";
import type { Build, BuildStage, StorageLocation } from "@/types";

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    body: unknown;
    userMessage: string;

    constructor(status: number, body: unknown, msg = "api error") {
      super(msg);
      this.status = status;
      this.body = body;
      this.userMessage = msg;
    }
  }

  return {
    ApiError,
    api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
  };
});

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ workspaceId: "ws-1" }) }));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}));

const build = {
  id: "build-1",
  project_id: "project-1",
  name: "Build 1",
  quantity: 10,
  status: "planned",
  archived_at: null,
  completed_at: null,
} as unknown as Build;

const storage = [
  { id: "tray-1", name: "Kitting tray", archived_at: null, is_full: false },
  { id: "full-bin", name: "Full bin", archived_at: null, is_full: true },
] as unknown as StorageLocation[];

const plan = {
  build_id: "build-1",
  build_stage_id: null,
  storage_location_id: "tray-1",
  storage_location_name: "Kitting tray",
  executed: false,
  lines: [
    {
      part_id: "part-1",
      part_name: "R1k 0402",
      project_entry_ids: ["entry-1"],
      required: 100,
      at_staging: 20,
      to_move: 80,
      moving: 50,
      short_by: 30,
      sources: [
        {
          storage_location_id: "shelf-a",
          storage_location_name: "Shelf A",
          lot_id: null,
          quantity: 50,
        },
      ],
    },
  ],
  totals: { lines: 1, moving: 50, short_by: 30, short_lines: 1 },
};

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    mutationCache: new MutationCache({}),
  });
}

function renderPanel(stages: BuildStage[] = []) {
  const client = makeClient();
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <BuildKitPanel
          buildId="build-1"
          build={build}
          stages={stages}
          storage={storage}
          isEditable
        />
      </QueryClientProvider>,
    ),
  };
}

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.mocked(api.get).mockResolvedValue(plan);
});

afterEach(() => cleanup());

describe("BuildKitPanel", () => {
  it("previews nothing until a staging location is picked", () => {
    renderPanel();

    expect(screen.getByText(/Pick a staging location/)).toBeTruthy();
    expect(api.get).not.toHaveBeenCalled();
  });

  it("shows what will move, from where, and the shortfall", async () => {
    renderPanel();

    fireEvent.change(screen.getByLabelText("Staging location"), {
      target: { value: "tray-1" },
    });

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(
        "/builds/build-1/kit-plan?storage_location_id=tray-1",
        expect.anything(),
      );
    });

    expect(await screen.findByText("R1k 0402")).toBeTruthy();
    // Required (attrition-adjusted), already-on-tray, and the source bin
    // are all surfaced — the operator has to see the top-up arithmetic.
    expect(screen.getByText("100")).toBeTruthy();
    expect(screen.getByText("20")).toBeTruthy();
    expect(screen.getByText("Shelf A (50)")).toBeTruthy();
    // The shortfall is prominent: partial availability still moves.
    expect(screen.getByText("30")).toBeTruthy();
    expect(screen.getByText(/1 line\(s\) short by 30/)).toBeTruthy();
  });

  it("offers only usable staging locations", () => {
    renderPanel();

    const options = Array.from(
      (screen.getByLabelText("Staging location") as HTMLSelectElement).options,
    ).map(o => o.value);
    expect(options).toEqual(["", "tray-1"]);
  });

  it("posts the kit once even on a double click", async () => {
    vi.mocked(api.post).mockImplementation(
      () =>
        new Promise(resolve =>
          setTimeout(() => resolve({ ...plan, executed: true }), 30),
        ),
    );
    renderPanel();

    fireEvent.change(screen.getByLabelText("Staging location"), {
      target: { value: "tray-1" },
    });

    const kit = await screen.findByRole("button", { name: "Kit to staging" });
    fireEvent.click(kit);
    fireEvent.click(kit);
    fireEvent.click(kit);

    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(api.post).toHaveBeenCalledWith("/builds/build-1/kit", {
      storage_location_id: "tray-1",
    });
  });

  it("kits a staged build stage by stage", async () => {
    const stages = [
      { id: "stage-1", name: "SMT", sequence: 0, status: "complete" },
      { id: "stage-2", name: "THT", sequence: 1, status: "planned" },
    ] as unknown as BuildStage[];
    renderPanel(stages);

    // Defaults to the next stage waiting to be consumed.
    expect((screen.getByLabelText("Stage") as HTMLSelectElement).value).toBe("stage-2");

    fireEvent.change(screen.getByLabelText("Staging location"), {
      target: { value: "tray-1" },
    });

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(
        "/builds/build-1/stages/stage-2/kit-plan?storage_location_id=tray-1",
        expect.anything(),
      );
    });
  });
});
