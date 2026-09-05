/**
 * DOM tests for the /c/:code scan landing page (Track A1).
 *
 * Pinned behaviours:
 *  - a resolved code redirects to the entity's own detail route
 *  - every codeable entity type has a mapped destination
 *  - a 404 renders the "code not found" copy, not a generic error banner
 *  - an entity_type this build doesn't know falls back to not-found
 *    rather than navigating somewhere wrong
 *  - a transient (non-404) failure offers Retry
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import CodeResolve from "../CodeResolve";

const getMock = vi.fn();

// `useWsKey` reads useAuth(); stub it so the test doesn't need an
// AuthProvider just to build a cache key.
vi.mock("@/lib/queryKeys", () => ({
  useWsKey: (...rest: unknown[]) => ["ws", "test-ws", ...rest],
}));

vi.mock("@/lib/api", async importOriginal => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, get: (...args: unknown[]) => getMock(...args) },
  };
});

function notFoundError() {
  return new ApiError(
    404,
    { data: null, status: { category: "not_found", message: "code not found" }, code: "code.not_found" },
    "code not found",
  );
}

function renderAt(code: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/c/${code}`]}>
        <Routes>
          <Route path="/c/:code" element={<CodeResolve />} />
          <Route path="/parts/:id/info" element={<div>PART PAGE</div>} />
          <Route path="/lots/:id/info" element={<div>LOT PAGE</div>} />
          <Route path="/storage/:id/info" element={<div>STORAGE PAGE</div>} />
          <Route path="/orders/:id" element={<div>ORDER PAGE</div>} />
          <Route path="/builds/:id" element={<div>BUILD PAGE</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  getMock.mockReset();
});

describe("CodeResolve", () => {
  it("asks the resolver for the scanned code", async () => {
    getMock.mockResolvedValue({ code: "ABCD1234", entity_type: "part", entity_id: "p-1" });
    renderAt("ABCD1234");
    await waitFor(() => expect(screen.getByText("PART PAGE")).toBeDefined());
    expect(getMock.mock.calls[0][0]).toBe("/codes/ABCD1234");
  });

  it.each([
    ["part", "PART PAGE"],
    ["lot", "LOT PAGE"],
    ["storage_location", "STORAGE PAGE"],
    ["order", "ORDER PAGE"],
    ["build", "BUILD PAGE"],
  ])("redirects a %s code to its detail page", async (entityType, marker) => {
    getMock.mockResolvedValue({ code: "ABCD1234", entity_type: entityType, entity_id: "e-1" });
    renderAt("ABCD1234");
    await waitFor(() => expect(screen.getByText(marker)).toBeDefined());
  });

  it("renders the not-found state for an unknown code", async () => {
    getMock.mockRejectedValue(notFoundError());
    renderAt("ZZZZZZZZ");
    await waitFor(() => expect(screen.getByText(/code not found/i)).toBeDefined());
    // The code itself is echoed so the user can check what was scanned.
    expect(screen.getByText("ZZZZZZZZ")).toBeDefined();
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });

  it("falls back to not-found for an entity_type this build doesn't know", async () => {
    getMock.mockResolvedValue({ code: "ABCD1234", entity_type: "spaceship", entity_id: "e-1" });
    renderAt("ABCD1234");
    await waitFor(() => expect(screen.getByText(/code not found/i)).toBeDefined());
  });

  it("offers Retry on a transient failure", async () => {
    getMock.mockRejectedValue(
      new ApiError(500, { data: null, status: { category: "server_error", message: "boom" } }, "boom"),
    );
    renderAt("ABCD1234");
    const retry = await waitFor(() => screen.getByRole("button", { name: /retry/i }));

    getMock.mockResolvedValue({ code: "ABCD1234", entity_type: "part", entity_id: "p-2" });
    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByText("PART PAGE")).toBeDefined());
  });
});
