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
