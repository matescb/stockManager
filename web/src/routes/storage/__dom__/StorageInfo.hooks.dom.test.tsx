/**
 * Regression for issue #284: StorageInfo violated React's Rules of Hooks
 * by calling its second `useQuery` *after* an `if (isError) return …`
 * early-return. When the first query errored, only one hook ran; on the
 * next render (after the error cleared) the component called two — and
 * React threw "Rendered more hooks than during the previous render."
 *
 * The fix moves both `useQuery` calls (and the derived `partName` map)
 * above the `isError` early-return so the hook count is constant across
 * every render, regardless of error state.
 *
 * This test mounts `StorageInfo` and toggles the first query between
 * error and success across re-renders. If the hook count ever changes,
 * React logs a `console.error` (and in strict mode throws) — both are
 * asserted against here.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Stub heavy deps so the test doesn't touch the real backend.
// ---------------------------------------------------------------------------
vi.mock("@/instrument", () => ({}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

vi.mock("@/lib/queryKeys", () => ({
  useWsKey: (...args: unknown[]) => ["ws-1", ...args],
  wsKeyOf: (...args: unknown[]) => args,
  archiveStorageKeys: () => [],
}));

// `api` is the only HTTP entry point per CLAUDE.md — stub it directly.
// We control success/failure for the storage-parts endpoint via the
// `storagePartsBehavior` ref below; the parts list always succeeds.
type StoragePartsRow = { part_id: string; lot_id: string | null; quantity: number };
const storagePartsBehavior: { mode: "error" | "success" } = { mode: "error" };

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
      get: vi.fn((url: string) => {
        if (url.startsWith("/storage/")) {
          if (storagePartsBehavior.mode === "error") {
            return Promise.reject(new ApiError(500, {}, "boom"));
          }
          return Promise.resolve<StoragePartsRow[]>([]);
        }
        if (url.startsWith("/parts")) {
          return Promise.resolve([{ id: "p1", name: "Resistor 10k" }]);
        }
        return Promise.resolve(null);
      }),
      post: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
      upload: vi.fn(),
    },
  };
});

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------
async function mountStorageInfo() {
  const { StorageInfo } = await import("../StorageDetail");
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  return {
    qc,
    result: render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/storage/s1/info"]}>
          <Routes>
            <Route path="/storage/:storageId/info" element={<StorageInfo />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

beforeEach(() => {
  vi.resetModules();
  storagePartsBehavior.mode = "error";
});

afterEach(() => {
  cleanup();
});

describe("StorageInfo — Rules of Hooks regression (#284)", () => {
  it("does not throw or warn when the first query toggles error → success", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    const { qc } = await mountStorageInfo();

    // First render: storage-parts query is in-flight. Wait for it to
    // resolve to the error state — the component should render the
    // inline error UI, not crash.
    await waitFor(() => {
      expect(screen.getByText(/Failed to load storage contents/)).toBeTruthy();
    });

    // Flip the query to success and force a refetch. Pre-fix this would
    // increase the hook count on the next render and produce React's
    // "Rendered more hooks than during the previous render." invariant.
    storagePartsBehavior.mode = "success";
    await qc.invalidateQueries();

    // Post-fix: the component recovers cleanly and renders the empty-
    // state row.
    await waitFor(() => {
      expect(screen.getByText("Empty.")).toBeTruthy();
    });

    // No React invariant violation should have been logged. We tolerate
    // unrelated act()/network noise by filtering to the specific Rules
    // of Hooks message.
    const hooksWarnings = consoleError.mock.calls.filter((args) =>
      args.some((a) => typeof a === "string" && /Rendered more hooks|Rules of Hooks/i.test(a)),
    );
    expect(hooksWarnings).toHaveLength(0);

    consoleError.mockRestore();
  });
});
