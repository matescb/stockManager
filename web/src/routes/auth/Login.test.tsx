// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, ApiError, type ApiErr } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import Login from "./Login";

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
}));

const mockPost = api.post as ReturnType<typeof vi.fn>;
const mockUseAuth = useAuth as ReturnType<typeof vi.fn>;

function renderLogin() {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/parts" element={<div>parts-page</div>} />
          <Route path="/signup" element={<div>signup-page</div>} />
          <Route path="/auth/request-reset" element={<div>request-reset-page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

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

describe("Login", () => {
  it("test_forgot_password_link_visible", () => {
    renderLogin();

    const link = screen.getByRole("link", { name: "Forgot password?" });
    expect(link.getAttribute("href")).toBe("/auth/request-reset");
  });

  it("test_429_renders_generic_message", async () => {
    const body = {
      data: null,
      status: { category: "validation_error", message: "too many failed login attempts" },
      code: "auth.account_locked",
      retry_after_seconds: 900,
    } as ApiErr;

    mockPost.mockRejectedValue(new ApiError(429, body, body.status.message));

    renderLogin();

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "locked@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "WrongPass!!X" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(
      await screen.findByText("Login failed. Check your credentials and try again."),
    ).toBeDefined();
    expect(screen.queryByText(/too many failed login attempts/i)).toBeNull();
    expect(screen.queryByText(/try again in/i)).toBeNull();
  });
});
