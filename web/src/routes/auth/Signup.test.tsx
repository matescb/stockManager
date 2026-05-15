// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import Signup from "./Signup";

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

function renderSignup() {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/signup"]}>
        <Routes>
          <Route path="/signup" element={<Signup />} />
          <Route path="/login" element={<div>login-page</div>} />
          <Route path="/parts" element={<div>parts-page</div>} />
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

describe("Signup", () => {
  it("validates_blocklisted_password_before_submit", () => {
    renderSignup();

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Test User" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "test@example.com" } });
    fireEvent.change(screen.getByLabelText("Password (min 8)"), {
      target: { value: "password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(screen.getByText("Password is too common. Choose a more unique password.")).toBeDefined();
    expect(mockPost).not.toHaveBeenCalled();
  });
});
