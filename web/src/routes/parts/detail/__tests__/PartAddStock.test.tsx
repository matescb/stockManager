// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "@/lib/api";
import PartAddStock from "../PartAddStock";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

function renderAddStock() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });

  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/parts/11111111-1111-1111-1111-111111111111/stock/add"]}>
        <Routes>
          <Route path="/parts/:partId/stock/add" element={<PartAddStock />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PartAddStock", () => {
  it("test_lot_expiry_submits", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockResolvedValue([]);
    const postSpy = vi.spyOn(api, "post").mockResolvedValue({});

    renderAddStock();

    await user.type(screen.getByLabelText("Quantity *"), "5");
    await user.type(screen.getByLabelText("Lot name (optional)"), "LOT-2026-001");
    await user.type(screen.getByLabelText("Lot expiration (optional)"), "2026-12-31");
    await user.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => {
      expect(postSpy).toHaveBeenCalledWith("/stock/add", {
        part_id: "11111111-1111-1111-1111-111111111111",
        quantity: 5,
        comments: undefined,
        lot: {
          name: "LOT-2026-001",
          expiration_date: "2026-12-31",
          serial_number: undefined,
        },
      });
    });
  });

  it("test_currency_regex rejects non-letter currency codes inline", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockResolvedValue([]);
    const postSpy = vi.spyOn(api, "post").mockResolvedValue({});

    renderAddStock();

    await user.type(screen.getByLabelText("Quantity *"), "5");
    await user.selectOptions(screen.getByLabelText("Price mode"), "per_component");
    await user.type(screen.getByLabelText("Unit price"), "0.12");
    const currency = screen.getByLabelText("Currency");
    await user.clear(currency);
    await user.type(currency, "EU1");
    await user.click(screen.getByRole("button", { name: "Add" }));

    expect(await screen.findByText("Currency must be a three-letter uppercase code.")).toBeDefined();
    expect(currency.getAttribute("aria-invalid")).toBe("true");
    await waitFor(() => {
      expect(postSpy).not.toHaveBeenCalled();
    });
  });
});
