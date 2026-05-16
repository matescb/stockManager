// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "@/lib/api";
import OrderDetail from "./OrderDetail";

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    userMessage: string;

    constructor(_status: number, _body: unknown, msg = "api error") {
      super(msg);
      this.userMessage = msg;
    }
  },
  api: {
    get: vi.fn(),
    post: vi.fn(),
    parsed: { post: vi.fn() },
    delete: vi.fn(),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

vi.mock("@/components/ConfirmDialog", () => ({
  useConfirm: () => vi.fn().mockResolvedValue(true),
}));

vi.mock("@/components/AttachmentsPanel", () => ({
  default: () => <div data-testid="attachments-panel" />,
}));

vi.mock("@/components/ActivityTimeline", () => ({
  default: () => <div data-testid="activity-timeline" />,
}));

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

function renderOrderDetail() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });

  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/orders/order-1"]}>
        <Routes>
          <Route path="/orders/:orderId" element={<OrderDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("OrderDetail receive", () => {
  it("submits lot name on receive lines", async () => {
    const user = userEvent.setup();
    const partId = "11111111-1111-1111-1111-111111111111";
    const entryId = "22222222-2222-2222-2222-222222222222";
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path === "/orders/order-1") {
        return Promise.resolve({
          order: {
            id: "order-1",
            name: "PO-100",
            order_type: "purchase",
            supplier: "Supplier",
            status: "open",
            ordered_on: null,
            expected_on: null,
            received_on: null,
            currency: "USD",
            comments: null,
            archived_at: null,
            totals: { ordered: 10, received: 0 },
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
          entries: [{
            id: entryId,
            order_id: "order-1",
            part_id: partId,
            name: null,
            quantity_ordered: 10,
            quantity_received: 0,
            unit_price: null,
            currency: null,
            comments: null,
            order_index: 0,
          }],
        });
      }
      if (path === "/parts?limit=200") return Promise.resolve([{ id: partId, name: "Part 1", serialized: false }]);
      if (path === "/storage") return Promise.resolve([]);
      return Promise.resolve(null);
    });
    vi.mocked(api.parsed.post).mockResolvedValue({
      order_id: "11111111-1111-1111-1111-111111111111",
      status: "partial",
      lots: [],
      stock_entries: [],
    });

    renderOrderDetail();

    await user.type(await screen.findByRole("spinbutton"), "4");
    await user.type(screen.getByPlaceholderText("PO-100#1"), "LOT-PO-100");
    await user.click(screen.getByRole("button", { name: "Receive" }));

    await waitFor(() => {
      expect(api.parsed.post).toHaveBeenCalledWith(
        "/orders/order-1/receive",
        expect.any(Object),
        {
          received_on: undefined,
          lines: [{
            order_entry_id: entryId,
            quantity: 4,
            storage_location_id: undefined,
            lot_name: "LOT-PO-100",
            serial_number: undefined,
          }],
        },
      );
    });
  });
});
