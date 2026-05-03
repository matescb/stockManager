/**
 * DOM tests for AttachmentsPanel error handling (#245 round 2).
 *
 * Pinned regression: pre-fix, a failed attachments fetch (5xx) rendered
 * the "No attachments yet." empty state, indistinguishable from a real
 * empty list (the symptom called out in issue #245). The fix routes the
 * error branch through `InlineQueryError`; the upload affordance must
 * stay interactive while the list region shows the pill so the user can
 * still attempt to upload (the data fetch may be flaky for unrelated
 * reasons that don't block writes).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// `useWsKey` reads `useAuth()` at render time. Stub it so the test
// doesn't need the full AuthProvider stack (which itself pulls
// react-router + Sentry).
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    me: null,
    loading: false,
    refresh: () => Promise.resolve(),
    logout: () => Promise.resolve(),
    workspaceId: "ws-test",
    switchWorkspace: () => Promise.resolve(),
  }),
}));

// ConfirmDialog uses portals — stub `useConfirm` to a no-op so render
// stays stable.
vi.mock("@/components/ConfirmDialog", () => ({
  useConfirm: () => () => Promise.resolve(false),
}));

import { api, ApiError } from "@/lib/api";
import AttachmentsPanel from "../AttachmentsPanel";

beforeEach(() => {
  cleanup();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children, client }: { children: React.ReactNode; client: QueryClient }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("AttachmentsPanel — error handling", () => {
  it("renders the inline error pill on a 5xx (not the empty state)", async () => {
    vi.spyOn(api, "get").mockRejectedValueOnce(
      new ApiError(
        503,
        { data: null, status: { category: "server_error", message: "Backend down" } },
        "Backend down",
      ),
    );
    const client = makeClient();

    render(
      <Wrapper client={client}>
        <AttachmentsPanel objectType="part" objectId="part-1" canWrite={true} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeDefined();
    });

    // The empty-state copy must NOT be present — that's the regression.
    expect(screen.queryByText(/no attachments yet/i)).toBeNull();
    // The error pill should mention "attachments" so the user knows
    // what failed.
    expect(screen.getByRole("alert").textContent?.toLowerCase()).toContain("attachments");
    // A retry affordance is present.
    expect(screen.getByRole("button", { name: /retry/i })).toBeDefined();
  });

  it("keeps the upload controls in the DOM while the list region shows the pill", async () => {
    vi.spyOn(api, "get").mockRejectedValueOnce(
      new ApiError(
        500,
        { data: null, status: { category: "server_error", message: "Boom" } },
        "Boom",
      ),
    );
    const client = makeClient();

    render(
      <Wrapper client={client}>
        <AttachmentsPanel objectType="part" objectId="part-1" canWrite={true} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeDefined();
    });

    // Upload affordance still rendered: the file input, the type
    // dropdown, and the Upload button are all present so the user can
    // still attempt a write while the read failed.
    expect(screen.getByLabelText(/^file$/i)).toBeDefined();
    expect(screen.getByLabelText(/^type$/i)).toBeDefined();
    expect(screen.getByRole("button", { name: /upload/i })).toBeDefined();
  });

  it("shows the empty state (no error pill) when the list is genuinely empty", async () => {
    vi.spyOn(api, "get").mockResolvedValueOnce([]);
    const client = makeClient();

    render(
      <Wrapper client={client}>
        <AttachmentsPanel objectType="part" objectId="part-1" canWrite={true} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByText(/no attachments yet/i)).toBeDefined();
    });
    // Crucially, no alert role is rendered — empty list is NOT an error.
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not render the pill on a 401 (auth bus handles redirect)", async () => {
    vi.spyOn(api, "get").mockRejectedValueOnce(
      new ApiError(
        401,
        { data: null, status: { category: "unauthenticated", message: "Session expired" } },
        "Session expired",
      ),
    );
    const client = makeClient();

    render(
      <Wrapper client={client}>
        <AttachmentsPanel objectType="part" objectId="part-1" canWrite={true} />
      </Wrapper>,
    );

    // Wait for the query to settle (it errored, so isError=true). The
    // pill suppresses 401 because the global QueryCache.onError fires
    // the auth bus and redirects to /login — flashing "couldn't load"
    // mid-bounce would confuse the user.
    await waitFor(() => {
      // The query has settled into the error branch — the empty state
      // copy is gone, but the alert is suppressed.
      expect(screen.queryByText(/loading…/i)).toBeNull();
    });
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
