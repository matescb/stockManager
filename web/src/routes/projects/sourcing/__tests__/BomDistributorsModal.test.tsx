// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BomDistributorsModal } from "../BomDistributorsModal";
import type { SourcingBomLine } from "../ProjectSourcingPage";

function line(overrides: Partial<SourcingBomLine> = {}): SourcingBomLine {
  return {
    project_entry_id: "entry-1",
    part_id: "part-1",
    part_name: "STM32",
    mpn: "STM32F103C8T6",
    required: 20,
    available: 0,
    substitute_ids: [],
    substitute_available: 0,
    short_by: 20,
    authorized_stock: 150,
    offers: [
      {
        mpn: "STM32F103C8T6",
        distributor: "DigiKey",
        sku: "DK-1",
        stock: 100,
        unit_price: "2.00",
        currency: "USD",
        unit_price_converted: "1.80",
        currency_displayed: "EUR",
        fx_converted: true,
        packaging: "Cut Tape",
        moq: 1,
        availability_text: "In Stock",
        quantity_multiple: 5,
        price_breaks: [
          { quantity: 1, unit_price: "2.00" },
          { quantity: 10, unit_price: "1.50" },
        ],
        price_breaks_converted: [
          { quantity: 1, unit_price: "1.80" },
          { quantity: 10, unit_price: "1.35" },
        ],
        url: "https://www.trustedparts.com/digikey/stm32",
        lifecycle_risk: "Low",
        supply_chain_risk: "Moderate",
        is_affected_by_tariff: true,
        rohs_compliance: [{ region: "EU", is_compliant: true, description: "Compliant" }],
      },
      {
        mpn: "STM32F103C8T6",
        distributor: "Mouser",
        sku: "MO-1",
        stock: 50,
        unit_price: "1.70",
        currency: "EUR",
        currency_displayed: "EUR",
        packaging: "Reel",
        moq: 10,
        availability_text: "Ships in 12 weeks",
        quantity_multiple: 1,
        price_breaks: [{ quantity: 10, unit_price: "1.70" }],
        url: "https://www.trustedparts.com/mouser/stm32",
        rohs_compliance: [{ region: "CN", is_compliant: false, description: "Missing" }],
      },
    ],
    best_offer: null,
    est_extended_cost: "36.00",
    lead_time_days: null,
    cache_hit: false,
    reason: "ok",
    risk_flags: [],
    ...overrides,
  };
}

function renderModal(props: Partial<ComponentProps<typeof BomDistributorsModal>> = {}) {
  const onClose = vi.fn();
  render(
    <BomDistributorsModal
      open
      onClose={onClose}
      line={line()}
      workspaceCurrency="EUR"
      {...props}
    />,
  );
  return { onClose };
}

beforeEach(() => {
  cleanup();
  localStorage.clear();
});

describe("BomDistributorsModal", () => {
  it("renders one row per distributor when offers populated", () => {
    renderModal();

    const dialog = screen.getByRole("dialog", { name: /STM32/ });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-labelledby")).toBeTruthy();
    expect(within(dialog).getByText("DigiKey")).toBeDefined();
    expect(within(dialog).getByText("Mouser")).toBeDefined();
    expect(within(dialog).getByText("2 distributors with stock; 2 total")).toBeDefined();
    expect(within(dialog).getByText("Powered by TrustedParts")).toBeDefined();
  });

  it("surfaces availability_text per row", () => {
    renderModal();

    expect(screen.getByText("In Stock")).toBeDefined();
    expect(screen.getByText("Ships in 12 weeks")).toBeDefined();
  });

  it("links open in new tab with noopener noreferrer", () => {
    renderModal();

    const link = screen.getByRole("link", { name: /DigiKey/ });
    expect(link.getAttribute("href")).toBe("https://www.trustedparts.com/digikey/stm32");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal();

    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on backdrop click", () => {
    const { onClose } = renderModal();

    fireEvent.mouseDown(screen.getByRole("dialog", { name: /STM32/ }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on X button click", async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal();

    await user.click(screen.getByRole("button", { name: "Close" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("moves focus into the dialog, traps Tab, and restores focus on close", async () => {
    const user = userEvent.setup();

    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Open drilldown</button>
          <a href="/outside">Outside link</a>
          <BomDistributorsModal
            open={open}
            onClose={() => setOpen(false)}
            line={line()}
            workspaceCurrency="EUR"
          />
        </>
      );
    }

    render(<Harness />);
    const openButton = screen.getByRole("button", { name: "Open drilldown" });
    await user.click(openButton);

    const dialog = screen.getByRole("dialog", { name: /STM32/ });
    const closeButton = within(dialog).getByRole("button", { name: "Close" });
    await waitFor(() => expect(document.activeElement).toBe(closeButton));

    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(dialog.contains(document.activeElement)).toBe(true);

    screen.getByRole("link", { name: "Outside link" }).focus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(dialog.contains(document.activeElement)).toBe(true);

    await user.click(closeButton);
    await waitFor(() => expect(document.activeElement).toBe(openButton));
  });

  it("prices use workspace currency formatting", () => {
    renderModal();

    expect(screen.getByText("1.8 EUR")).toBeDefined();
    expect(screen.getByText("1+ 1.8 EUR")).toBeDefined();
    expect(screen.getByText("10+ 1.35 EUR")).toBeDefined();
    expect(screen.queryByText("2 USD")).toBeNull();
  });

  it("renders empty-state when offers array is empty", () => {
    renderModal({ line: line({ offers: [] }) });

    expect(screen.getByText("No distributor offers for this BOM line.")).toBeDefined();
    expect(screen.getByText("0 distributors with stock; 0 total")).toBeDefined();
  });
});
