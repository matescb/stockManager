import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/lib/api";
import PartLayout from "../PartLayout";

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

vi.mock("@/components/EntityHeader", () => ({
  default: ({ title }: { title: string }) => <div data-testid="entity-header">{title}</div>,
}));

vi.mock("@/components/SubNav", () => ({
  default: () => <nav />,
}));

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderPartLayout(initialEntry: string, routePath: string) {
  const client = makeClient();
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path={routePath} element={<PartLayout />} />
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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PartLayout query keys", () => {
  it("does not mount queries without a part id route param", () => {
    const { client } = renderPartLayout("/parts", "/parts");

    expect(screen.getByText("Missing part id.")).toBeTruthy();
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    expect(api.get).not.toHaveBeenCalled();
  });

  it("uses only defined key segments while the part is loading", () => {
    vi.mocked(api.get).mockImplementation(() => new Promise<never>(() => {}));

    const { client } = renderPartLayout("/parts/part-1", "/parts/:partId");
    const keys = client.getQueryCache().getAll().map(query => query.queryKey);

    expect(keys).toEqual([["ws", "ws-1", "part", "part-1"]]);
    expect(keys.some(containsUndefined)).toBe(false);
  });
});
