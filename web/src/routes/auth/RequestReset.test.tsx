// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, ApiError, type ApiErr } from "@/lib/api";
import RequestReset from "./RequestReset";

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

function renderRequestReset() {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/auth/request-reset"]}>
        <Routes>
          <Route path="/auth/request-reset" element={<RequestReset />} />
          <Route path="/login" element={<div>login-page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RequestReset", () => {
  it("test_happy_path_acknowledges_generically", async () => {
    mockPost.mockResolvedValue({ status: "accepted" });

    renderRequestReset();

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "u@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByText("u@example.com")).toBeDefined();
    expect(mockPost).toHaveBeenCalledWith(
      "/auth/request-password-reset",
      { email: "u@example.com" },
    );
  });

  it("test_throttled_after_3_attempts", async () => {
    const body = {
      data: null,
      status: { category: "validation_error", message: "rate limit exceeded" },
      code: "rate_limited",
    } as ApiErr;
    mockPost.mockRejectedValue(new ApiError(429, body, body.status.message));

    renderRequestReset();

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "u@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(
      await screen.findByText("Some fields don't look right. Check the form and retry."),
    ).toBeDefined();
  });
});
