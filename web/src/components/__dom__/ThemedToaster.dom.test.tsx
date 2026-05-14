import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import ThemedToaster from "../ThemedToaster";

vi.mock("@/lib/theme", () => ({
  useTheme: () => ({ resolved: "light" }),
}));

afterEach(() => {
  act(() => {
    toast.dismiss();
  });
  cleanup();
});

describe("ThemedToaster", () => {
  it("test_request_id_shown_on_error", async () => {
    const error = new ApiError(
      500,
      {
        data: null,
        status: { category: "server_error", message: "boom" },
        request_id: "req-aud-062",
      },
      "boom",
    );

    render(<ThemedToaster />);
    act(() => {
      toast.error(error.userMessage);
    });

    expect(await screen.findByText(/Request ID: req-aud-062/i)).toBeDefined();
  });
});
