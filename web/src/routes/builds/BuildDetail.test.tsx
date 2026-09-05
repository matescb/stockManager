// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { ApiError, api } from "@/lib/api";
import { authBus } from "@/lib/queryKeys";
import BuildDetail from "./BuildDetail";

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
    api: {
      get: vi.fn(),
      post: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
      upload: vi.fn(),
    },
  };
});

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

vi.mock("@/components/AttachmentsPanel", () => ({
  default: () => <div data-testid="attachments-panel" />,
}));

vi.mock("@/components/ActivityTimeline", () => ({
  default: () => <div data-testid="activity-timeline" />,
}));

vi.mock("@/routes/projects/sourcing/SourceBomButton", () => ({
  SourceBomButton: () => <button type="button">Source BOM</button>,
}));

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

const buildDetail = {
  build: {
    id: "build-1",
    project_id: "project-1",
    name: "Build 1",
    quantity: 1,
    status: "planned",
    archived_at: null,
    completed_at: null,
  },
  shortage: [],
};

function mockReads() {
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path === "/builds/build-1") return Promise.resolve(buildDetail);
    if (path === "/projects/project-1") return Promise.resolve({ id: "project-1", name: "Project 1" });
    if (path === "/projects/project-1/entries") return Promise.resolve([]);
    if (path === "/parts?limit=200") return Promise.resolve([]);
    if (path === "/storage") return Promise.resolve([]);
    return Promise.resolve(null);
  });
}

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    mutationCache: new MutationCache({
      onError: (err) => {
        if (err instanceof ApiError && err.status === 401) {
          authBus.emit("unauthorized");
        }
      },
    }),
  });
}

function renderBuildDetail(initialEntry = "/builds/build-1", routePath = "/builds/:buildId") {
  const client = makeClient();
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path={routePath} element={<BuildDetail />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

function containsUndefined(value: unknown): boolean {
  if (value === undefined) return true;
  if (Array.isArray(value)) return value.some(containsUndefined);
  if (value && typeof value === "object") {
    return Object.values(value as Record<string, unknown>).some(containsUndefined);
  }
  return false;
}

describe("BuildDetail query keys", () => {
  it("does not mount queries without a build id route param", () => {
    const { client } = renderBuildDetail("/builds", "/builds");

    expect(screen.getByText("Missing build id.")).toBeTruthy();
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    expect(api.get).not.toHaveBeenCalled();
  });

  it("does not register undefined key segments while the build is loading", () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === "/builds/build-1") return new Promise<never>(() => {});
      return Promise.resolve(null);
    });

    const { client } = renderBuildDetail();
    const keys = client.getQueryCache().getAll().map(query => query.queryKey);

    expect(keys).toEqual([["ws", "ws-1", "build", "build-1"]]);
    expect(keys.some(containsUndefined)).toBe(false);
  });
});

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
  mockReads();
});

afterEach(() => {
  cleanup();
});

describe("BuildDetail archive mutation", () => {
  it("test_double_click_archive_single_post", async () => {
    vi.mocked(api.post).mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(null), 30)),
    );

    renderBuildDetail();

    const archive = await screen.findByRole("button", { name: "Archive" });
    fireEvent.click(archive);

    await waitFor(() => {
      expect((archive as HTMLButtonElement).disabled).toBe(true);
    });

    fireEvent.click(archive);
    fireEvent.click(archive);

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledTimes(1);
    });
    expect(api.post).toHaveBeenCalledWith("/builds/build-1/archive");
  });

  it("routes archive 401s through the auth bus", async () => {
    const heard = vi.fn();
    const off = authBus.on((event) => heard(event));
    vi.mocked(api.post).mockRejectedValue(
      new ApiError(401, { data: null, status: { category: "unauthenticated", message: "expired" } }, "expired"),
    );

    renderBuildDetail();

    fireEvent.click(await screen.findByRole("button", { name: "Archive" }));

    await waitFor(() => {
      expect(heard).toHaveBeenCalledWith("unauthorized");
    });
    expect(api.post).toHaveBeenCalledTimes(1);

    off();
  });
});

describe("BuildDetail consumption plan", () => {
  it("invalidates report queries on consume", async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === "/builds/build-1") {
        return Promise.resolve({
          build: buildDetail.build,
          shortage: [{
            project_entry_id: "entry-1",
            part_id: "part-1",
            part_name: "Part 1",
            required: 2,
            available: 2,
            substitute_ids: [],
            substitute_available: 0,
            short_by: 0,
          }],
        });
      }
      if (path === "/projects/project-1") return Promise.resolve({ id: "project-1", name: "Project 1" });
      if (path === "/projects/project-1/entries") {
        return Promise.resolve([{
          id: "entry-1",
          project_id: "project-1",
          entry_type: "part",
          part_id: "part-1",
          meta_part_id: null,
          name: null,
          quantity: 2,
          comments: null,
          designators: [],
          cad_footprint: null,
          cad_key: null,
          dnp: false,
          order_index: 0,
        }]);
      }
      if (path === "/parts?limit=200") return Promise.resolve([{ id: "part-1", name: "Part 1" }]);
      if (path === "/storage") return Promise.resolve([]);
      return Promise.resolve(null);
    });
    vi.mocked(api.post).mockResolvedValue(null);

    const { client } = renderBuildDetail();
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");

    fireEvent.click(await screen.findByRole("button", { name: "Auto-fill" }));
    fireEvent.click(screen.getByRole("button", { name: "Consume & complete build" }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith("/builds/build-1/consume", expect.any(Object));
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["ws", "ws-1", "report", "low-stock"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["ws", "ws-1", "report", "stock-value"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["ws", "ws-1", "report", "expiring"] });
  });

  it("submits output lot and storage fields", async () => {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === "/builds/build-1") {
        return Promise.resolve({
          build: buildDetail.build,
          shortage: [{
            project_entry_id: "entry-1",
            part_id: "part-1",
            part_name: "Part 1",
            required: 2,
            available: 2,
            substitute_ids: [],
            substitute_available: 0,
            short_by: 0,
          }],
        });
      }
      if (path === "/projects/project-1") return Promise.resolve({ id: "project-1", name: "Project 1" });
      if (path === "/projects/project-1/entries") {
        return Promise.resolve([{
          id: "entry-1",
          project_id: "project-1",
          entry_type: "part",
          part_id: "part-1",
          meta_part_id: null,
          name: null,
          quantity: 2,
          comments: null,
          designators: [],
          cad_footprint: null,
          cad_key: null,
          dnp: false,
          order_index: 0,
        }]);
      }
      if (path === "/parts?limit=200") return Promise.resolve([{ id: "part-1", name: "Part 1" }]);
      if (path === "/storage") {
        return Promise.resolve([
          { id: "storage-1", name: "Bin A", archived_at: null, is_full: false },
        ]);
      }
      return Promise.resolve(null);
    });
    vi.mocked(api.post).mockResolvedValue(null);

    renderBuildDetail();

    fireEvent.click(await screen.findByRole("button", { name: "Auto-fill" }));
    fireEvent.change(screen.getByLabelText("Output lot name"), { target: { value: "BUILD-LOT-1" } });
    fireEvent.change(screen.getByLabelText("Output storage"), { target: { value: "storage-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Consume & complete build" }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith("/builds/build-1/consume", {
        lines: [{
          project_entry_id: "entry-1",
          part_id: "part-1",
          quantity: 2,
          storage_location_id: undefined,
        }],
        output_lot_name: "BUILD-LOT-1",
        output_storage_location_id: "storage-1",
      });
    });
  });
});

// --- Multi-stage builds (Track B2) ------------------------------------------

const stagedEntry = {
  id: "entry-1",
  project_id: "project-1",
  entry_type: "part",
  part_id: "part-1",
  meta_part_id: null,
  name: null,
  quantity: 10,
  comments: null,
  designators: [],
  cad_footprint: null,
  cad_key: null,
  dnp: false,
  order_index: 0,
};

function stage(id: string, sequence: number, status: string, required: number) {
  return {
    id,
    build_id: "build-1",
    name: `Stage ${sequence + 1}`,
    sequence,
    status,
    started_at: null,
    completed_at: null,
    comments: null,
    lines: [{ id: `line-${id}`, project_entry_id: "entry-1", portion_pct: 50 }],
    shortage: [{
      project_entry_id: "entry-1",
      part_id: "part-1",
      part_name: "Part 1",
      attrition_pct: 0,
      portion_pct: 50,
      required,
      available: 100,
      substitute_ids: [],
      substitute_available: 0,
      short_by: 0,
    }],
    created_at: "2026-09-05T00:00:00Z",
    updated_at: "2026-09-05T00:00:00Z",
  };
}

function mockStagedReads(stages: unknown[]) {
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path === "/builds/build-1") return Promise.resolve(buildDetail);
    if (path === "/builds/build-1/stages") return Promise.resolve(stages);
    if (path === "/projects/project-1") return Promise.resolve({ id: "project-1", name: "Project 1" });
    if (path === "/projects/project-1/entries") return Promise.resolve([stagedEntry]);
    if (path === "/parts?limit=200") return Promise.resolve([{ id: "part-1", name: "Part 1" }]);
    if (path === "/storage") return Promise.resolve([]);
    return Promise.resolve(null);
  });
}

describe("BuildDetail assembly stages", () => {
  it("shows the single-pass consumption plan when the build has no stages", async () => {
    mockStagedReads([]);

    renderBuildDetail();

    expect(await screen.findByText("Assembly stages")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Consume & complete build" })).toBeTruthy();
  });

  it("hides the whole-build plan and consumes per stage once stages exist", async () => {
    mockStagedReads([stage("stage-1", 0, "planned", 50), stage("stage-2", 1, "planned", 50)]);
    vi.mocked(api.post).mockResolvedValue(null);

    renderBuildDetail();

    // The whole-build endpoint refuses staged builds, so its card is gone.
    expect(await screen.findByRole("button", { name: "Consume stage" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Consume & complete build" })).toBeNull();

    // Only the next incomplete stage offers a consume action.
    const consumeButtons = screen.getAllByRole("button", { name: "Consume stage" });
    expect(consumeButtons).toHaveLength(1);

    fireEvent.click(consumeButtons[0]);

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith("/builds/build-1/stages/stage-1/consume", {
        lines: [{
          project_entry_id: "entry-1",
          part_id: "part-1",
          quantity: 50,
          storage_location_id: undefined,
        }],
      });
    });
  });

  it("offers the next stage after an earlier one is complete", async () => {
    mockStagedReads([stage("stage-1", 0, "complete", 50), stage("stage-2", 1, "planned", 50)]);
    vi.mocked(api.post).mockResolvedValue(null);

    renderBuildDetail();

    fireEvent.click(await screen.findByRole("button", { name: "Consume stage" }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        "/builds/build-1/stages/stage-2/consume",
        expect.any(Object),
      );
    });
  });

  it("creates a stage from the selected BOM lines", async () => {
    mockStagedReads([]);
    vi.mocked(api.post).mockResolvedValue(null);

    renderBuildDetail();

    fireEvent.click(await screen.findByRole("button", { name: "+ Add stage" }));
    fireEvent.change(screen.getByLabelText("Stage name"), { target: { value: "SMT" } });
    fireEvent.click(await screen.findByLabelText("Include Part 1"));
    fireEvent.click(screen.getByRole("button", { name: "Add stage" }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith("/builds/build-1/stages", {
        name: "SMT",
        lines: [{ project_entry_id: "entry-1", portion_pct: 100 }],
      });
    });
  });
});
