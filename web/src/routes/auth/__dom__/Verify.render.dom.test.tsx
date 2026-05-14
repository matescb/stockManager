/**
 * DOM render test for Verify.tsx (FE2-020 follow-up / issue #249).
 *
 * Pinned behaviour:
 *  - When api.post() throws an ApiError, the component renders the
 *    user-safe e.userMessage (category-mapped) rather than the raw
 *    server-side e.message.
 *
 * This test was introduced to guard against regressions after the fix
 * that replaced e.message with e.userMessage at the Verify callsite.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import Verify from "../Verify";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      post: vi.fn(),
    },
  };
});

vi.mock("@/lib/auth", () => ({
  useAuth: vi.fn(),
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

const mockPost = api.post as ReturnType<typeof vi.fn>;
const mockUseAuth = useAuth as ReturnType<typeof vi.fn>;

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
  mockUseAuth.mockReturnValue({
    me: null,
    loading: false,
    refresh: vi.fn().mockResolvedValue(undefined),
    logout: vi.fn(),
    workspaceId: null,
    switchWorkspace: vi.fn(),
  });
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderVerify(search = "?id=x&token=y") {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/verify", search }]}>
      <Routes>
        <Route path="/verify" element={<Verify />} />
        <Route path="/parts" element={<div>parts-page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Verify — ApiError render", () => {
  it("shows user-safe message and does NOT leak raw server detail", async () => {
    const RAW_SERVER_MSG = "raw psycopg detail";
    const USER_SAFE_MSG = "Something went wrong. Try again, or refresh.";

    mockPost.mockRejectedValue(
      new ApiError(
        500,
        { data: null, status: { category: "server_error", message: RAW_SERVER_MSG } },
        RAW_SERVER_MSG,
      ),
    );

    renderVerify("?id=x&token=y");

    await waitFor(() => {
      expect(screen.queryByText("Verifying your email…")).toBeNull();
    });

    const errorContainer = await screen.findByText(USER_SAFE_MSG);
    expect(errorContainer).toBeDefined();

    // The raw server string must NOT appear anywhere in the rendered output.
    expect(screen.queryByText(new RegExp(RAW_SERVER_MSG))).toBeNull();
  });

  it("shows an expired-link message for 410", async () => {
    mockPost.mockRejectedValue(
      new ApiError(
        410,
        { data: null, status: { category: "not_found", message: "expired" } },
        "expired",
      ),
    );

    renderVerify("?id=x&token=y");

    expect(await screen.findByText("Verification link expired.")).toBeDefined();
    expect(screen.queryByText("Verification link not found.")).toBeNull();
  });

  it("shows an unknown-link message for 404", async () => {
    mockPost.mockRejectedValue(
      new ApiError(
        404,
        { data: null, status: { category: "not_found", message: "unknown" } },
        "unknown",
      ),
    );

    renderVerify("?id=x&token=y");

    expect(await screen.findByText("Verification link not found.")).toBeDefined();
    expect(screen.queryByText("Verification link expired.")).toBeNull();
  });
});
