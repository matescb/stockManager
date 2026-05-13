// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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

function renderModal(onClose = vi.fn(), onSelect = vi.fn()) {
  render(
    <OverrideOfferModal
      line={line()}
      onSelect={onSelect}
      onClose={onClose}
    />,
  );
  return { onClose, onSelect };
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

  it("renders alternate offers in the shared DataTable", () => {
    renderModal();

    const table = screen.getByRole("table");
    expect(within(table).getByText("Mouser")).toBeDefined();
    expect(within(table).getByText("1.80 USD")).toBeDefined();
    expect(screen.getByPlaceholderText("Search offers...")).toBeDefined();
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeDefined();
  });

  it("keeps selecting an alternate offer wired through the table action", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    renderModal(vi.fn(), onSelect);

    await user.click(screen.getByRole("button", { name: "Select" }));

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0].id).toBe("line-1");
    expect(onSelect.mock.calls[0][1].distributor).toBe("Mouser");
  });

  it("keeps the current offer visible with a disabled hint row", () => {
    render(
      <OverrideOfferModal
        line={{
          ...line(),
          available_offers: [
            {
              mpn: "STM32F103C8T6",
              distributor: "DigiKey",
              stock: 100,
              unit_price: "2.00",
              currency: "USD",
              packaging: "Cut Tape",
              moq: 1,
              lead_time_days: 3,
              url: "https://example.com/digikey/stm32",
            },
          ],
        }}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("Current selection")).toBeDefined();
    expect(
      (screen.getByRole("button", { name: "Current" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
