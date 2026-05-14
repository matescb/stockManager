// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfirmDialogProvider, useConfirm } from "./ConfirmDialog";

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function ConfirmHarness() {
  const confirm = useConfirm();
  return (
    <button
      type="button"
      onClick={() => {
        void confirm({
          title: "Delete part",
          message: "Delete this part?",
          severity: "danger",
        });
      }}
    >
      Open confirm
    </button>
  );
}

function renderConfirmDialog() {
  return render(
    <ConfirmDialogProvider>
      <button type="button">Outside</button>
      <ConfirmHarness />
    </ConfirmDialogProvider>,
  );
}

describe("ConfirmDialog", () => {
  it("labels the dialog with aria-labelledby", async () => {
    const user = userEvent.setup();
    renderConfirmDialog();

    await user.click(screen.getByRole("button", { name: "Open confirm" }));

    const dialog = await screen.findByRole("dialog", { name: "Delete part" });
    const labelledBy = dialog.getAttribute("aria-labelledby");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(labelledBy).toBeTruthy();
    expect(labelledBy ? document.getElementById(labelledBy)?.textContent : null).toBe("Delete part");
  });

  it("test_focus_trapped", async () => {
    const user = userEvent.setup();
    renderConfirmDialog();

    await user.click(screen.getByRole("button", { name: "Open confirm" }));

    const dialog = await screen.findByRole("dialog", { name: "Delete part" });
    const cancelButton = within(dialog).getByRole("button", { name: "Cancel" });
    const confirmButton = within(dialog).getByRole("button", { name: "Delete" });
    await waitFor(() => expect(document.activeElement).toBe(confirmButton));

    fireEvent.keyDown(window, { key: "Tab" });
    expect(document.activeElement).toBe(cancelButton);

    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(confirmButton);

    screen.getByRole("button", { name: "Outside" }).focus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(document.activeElement).toBe(cancelButton);
  });
});
