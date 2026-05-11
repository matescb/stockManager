// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OverrideOfferModal from "../OverrideOfferModal";
import type { PurchasePlanLine } from "../purchasePlanTypes";

function line(): PurchasePlanLine {
  return {
    id: "line-1",
    part_id: "part-1",
    mpn_searched: "STM32F103C8T6",
    required_qty: 20,
    internal_available_qty: 0,
    shortage_qty: 20,
    selected_distributor: "DigiKey",
    selected_qty: 20,
    selected_unit_price: "2.00",
    selected_currency: "USD",
    selected_packaging: "Cut Tape",
    selected_moq: 1,
    selected_lead_time_days: 3,
    available_offers: [{
      mpn: "STM32F103C8T6",
      distributor: "Mouser",
      stock: 100,
      unit_price: "1.80",
      currency: "USD",
      packaging: "Reel",
      moq: 10,
      lead_time_days: 5,
      url: "https://example.com/mouser/stm32",
    }],
    risk_flags: [],
  };
}

function renderModal(onClose = vi.fn()) {
  render(
    <OverrideOfferModal
      line={line()}
      onSelect={vi.fn()}
      onClose={onClose}
    />,
  );
  return { onClose };
}

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("OverrideOfferModal", () => {
  it("renders with shared dialog semantics", () => {
    renderModal();

    const dialog = screen.getByRole("dialog", { name: "Override offer" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-labelledby")).toBeTruthy();
  });

  it("ESC closes the override modal", async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal();

    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on backdrop click", () => {
    const { onClose } = renderModal();

    fireEvent.mouseDown(screen.getByRole("dialog", { name: "Override offer" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
