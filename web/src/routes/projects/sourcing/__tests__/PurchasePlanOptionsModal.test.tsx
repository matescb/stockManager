// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PurchasePlanOptionsModal from "../PurchasePlanOptionsModal";

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PurchasePlanOptionsModal", () => {
  it("renders with shared dialog semantics", () => {
    render(
      <PurchasePlanOptionsModal
        open
        buildQuantity={3}
        baseRequest={{ build_quantity: 3, country: "US", currency: "USD" }}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "Generate purchase plan" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-labelledby")).toBeTruthy();
  });

  it("submits with default strategy preferred_first", async () => {
    const onSubmit = vi.fn();
    render(
      <PurchasePlanOptionsModal
        open
        buildQuantity={3}
        baseRequest={{ build_quantity: 3, country: "US", currency: "USD" }}
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Generate" }));

    expect(onSubmit).toHaveBeenCalledWith({
      build_quantity: 3,
      country: "US",
      currency: "USD",
      strategy: "preferred_first",
      price_tolerance_pct: "5",
    });
  });

  it("advanced options toggle reveals max_distributors and tolerance fields", async () => {
    render(
      <PurchasePlanOptionsModal
        open
        buildQuantity={1}
        baseRequest={{ build_quantity: 1 }}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText("Max distributors")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Advanced options" }));

    expect(screen.getByLabelText("Max distributors")).toBeDefined();
    expect(screen.getByLabelText("Tolerance %")).toBeDefined();
  });

  it("ESC closes the options modal", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <PurchasePlanOptionsModal
        open
        buildQuantity={3}
        baseRequest={{ build_quantity: 3, country: "US", currency: "USD" }}
        onClose={onClose}
        onSubmit={vi.fn()}
      />,
    );

    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on backdrop click", () => {
    const onClose = vi.fn();
    render(
      <PurchasePlanOptionsModal
        open
        buildQuantity={3}
        baseRequest={{ build_quantity: 3, country: "US", currency: "USD" }}
        onClose={onClose}
        onSubmit={vi.fn()}
      />,
    );

    fireEvent.mouseDown(screen.getByRole("dialog", { name: "Generate purchase plan" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
