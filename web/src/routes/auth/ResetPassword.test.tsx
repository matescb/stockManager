// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, ApiError, type ApiErr } from "@/lib/api";
import ResetPassword from "./ResetPassword";

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

const mockPost = api.post as ReturnType<typeof vi.fn>;

function renderResetPassword(initialPath = "/auth/reset-password?token=abc123") {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/auth/reset-password" element={<ResetPassword />} />
          <Route path="/login" element={<div>login-page</div>} />
          <Route path="/auth/request-reset" element={<div>request-reset-page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ResetPassword", () => {
  it("test_happy_path", async () => {
    mockPost.mockResolvedValue({ status: "password_reset" });

    renderResetPassword();

    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "NewResetPass-2026!!" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save new password" }));

    expect(await screen.findByText("Your password has been updated.")).toBeDefined();
    expect(mockPost).toHaveBeenCalledWith(
      "/auth/reset-password",
      { token: "abc123", new_password: "NewResetPass-2026!!" },
    );
  });

  it("renders_invalid_expired_and_used_states", async () => {
    const expiredBody = {
      data: null,
      status: { category: "validation_error", message: "password reset link expired" },
      code: "auth.reset_expired",
    } as ApiErr;
    mockPost.mockRejectedValue(new ApiError(400, expiredBody, expiredBody.status.message));

    renderResetPassword();

    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "NewResetPass-2026!!" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save new password" }));

    expect(await screen.findByText("This reset link has expired.")).toBeDefined();
  });

  it("validates_password_strength_before_submit", () => {
    renderResetPassword();

    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "aaaaaaaa" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save new password" }));

    expect(screen.getByText("Use a less repetitive password.")).toBeDefined();
    expect(mockPost).not.toHaveBeenCalled();
  });
});
