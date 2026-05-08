// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { ApiError, api } from "@/lib/api";
import type { Order } from "@/types";
import {
  buildComplianceSafeOrderLineNote,
  CreateOrderLineModal,
  type CreateOrderLineSource,
} from "../CreateOrderLineModal";

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ workspaceId: "ws-1" }),
}));

const source: CreateOrderLineSource = {
  partId: "11111111-1111-1111-1111-111111111111",
  distributor: "DigiKey",
  packaging: "Tape",
  leadTimeDays: 3,
  fetchedAt: "2026-05-08T12:00:00+00:00",
  quantity: 25,
  unitPrice: 1.23,
  currency: "EUR",
  productUrl: "https://www.trustedparts.com/digikey/stm32",
};

const draftOrder: Order = {
  id: "22222222-2222-2222-2222-222222222222",
  name: "May sourcing",
  order_type: "purchase",
  supplier: "DigiKey",
  status: "draft",
  ordered_on: null,
  expected_on: null,
  received_on: null,
  currency: "EUR",
  comments: null,
  archived_at: null,
  totals: { ordered: 0, received: 0 },
  created_at: "2026-05-08T12:00:00+00:00",
  updated_at: "2026-05-08T12:00:00+00:00",
};

function apiError(status: number, message: string) {
  return new ApiError(
    status,
    {
      data: null,
      status: { category: status === 422 ? "validation_error" : "server_error", message },
    },
    message,
  );
}

function renderModal(onClose = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <CreateOrderLineModal open source={source} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { invalidateSpy, onClose };
}

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("CreateOrderLineModal", () => {
  it("submitting with an existing draft order appends the line", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockResolvedValue([draftOrder]);
    const postSpy = vi.spyOn(api, "post").mockResolvedValue({
      id: "entry-1",
      order_id: draftOrder.id,
      part_id: source.partId,
      name: null,
      quantity_ordered: 25,
      quantity_received: 0,
      unit_price: 1.23,
      currency: "EUR",
      comments: buildComplianceSafeOrderLineNote(source),
      order_index: 0,
    });
    const onClose = vi.fn();
    const { invalidateSpy } = renderModal(onClose);

    await screen.findByRole("dialog", { name: "Create order line" });
    await waitFor(() => {
      expect((screen.getByLabelText("Order") as HTMLSelectElement).value).toBe(draftOrder.id);
    });
    await user.click(screen.getByRole("button", { name: "Create order line" }));

    await waitFor(() => {
      expect(postSpy).toHaveBeenCalledWith(`/orders/${draftOrder.id}/entries`, {
        part_id: source.partId,
        quantity_ordered: 25,
        unit_price: "1.23",
        currency: "EUR",
        comments: buildComplianceSafeOrderLineNote(source),
      });
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["ws", "ws-1", "orders"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["ws", "ws-1", "order", draftOrder.id] });
    expect(toast.success).toHaveBeenCalledWith(
      "Order line created",
      expect.objectContaining({
        action: expect.objectContaining({ label: "Open May sourcing" }),
      }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("submitting with create new order posts to /orders with one entry", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockResolvedValue([draftOrder]);
    const postSpy = vi.spyOn(api, "post").mockResolvedValue({
      ...draftOrder,
      id: "33333333-3333-3333-3333-333333333333",
      name: "TrustedParts DigiKey 2026-05-08",
      status: "open",
      totals: { ordered: 25, received: 0 },
    });

    renderModal();

    await screen.findByRole("dialog", { name: "Create order line" });
    const orderSelect = screen.getByLabelText("Order");
    await waitFor(() => {
      expect((orderSelect as HTMLSelectElement).value).toBe(draftOrder.id);
    });
    await user.selectOptions(orderSelect, "__create_new__");
    const orderName = await screen.findByLabelText("Order name");
    await user.clear(orderName);
    await user.type(orderName, "TrustedParts DigiKey 2026-05-08");
    await user.click(screen.getByRole("button", { name: "Create order line" }));

    await waitFor(() => {
      expect(postSpy).toHaveBeenCalledWith("/orders", {
        name: "TrustedParts DigiKey 2026-05-08",
        order_type: "purchase",
        supplier: "DigiKey",
        currency: "EUR",
        entries: [
          {
            part_id: source.partId,
            quantity_ordered: 25,
            unit_price: "1.23",
            currency: "EUR",
            comments: buildComplianceSafeOrderLineNote(source),
          },
        ],
      });
    });
  });

  it("the saved note contains compliance-safe metadata only", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockResolvedValue([draftOrder]);
    const postSpy = vi.spyOn(api, "post").mockResolvedValue({
      id: "entry-1",
      order_id: draftOrder.id,
    });

    renderModal();

    await screen.findByRole("dialog", { name: "Create order line" });
    expect(screen.getByRole("link", { name: "Open distributor page" }).getAttribute("href"))
      .toBe(source.productUrl);
    await user.clear(screen.getByLabelText("Note"));
    await user.type(
      screen.getByLabelText("Note"),
      `${buildComplianceSafeOrderLineNote(source)} ${source.productUrl}`,
    );
    await user.click(screen.getByRole("button", { name: "Create order line" }));

    await waitFor(() => {
      expect(postSpy).toHaveBeenCalled();
    });
    const payload = postSpy.mock.calls[0][1] as { comments?: string; product_url?: string };
    expect(payload.comments).toContain("From TrustedParts: distributor=DigiKey");
    expect(payload.comments).toContain("packaging=Tape");
    expect(payload.comments).toContain("lead_time=3 days");
    expect(payload.comments).toContain("fetched_at=2026-05-08T12:00:00+00:00");
    expect(payload.comments).not.toContain(source.productUrl);
    expect(payload.product_url).toBeUndefined();
  });

  it("error path renders form-level error message", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockResolvedValue([draftOrder]);
    vi.spyOn(api, "post").mockRejectedValue(apiError(422, "bad payload"));

    renderModal();

    await screen.findByRole("dialog", { name: "Create order line" });
    await user.click(screen.getByRole("button", { name: "Create order line" }));

    expect((await screen.findByRole("alert")).textContent).toBe("Some fields don't look right. Check the form and retry.");
  });
});
