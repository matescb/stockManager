// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Modal } from "../Modal";

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Modal", () => {
  it("closes on ESC key", () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Demo modal">
        <button type="button">Close</button>
      </Modal>,
    );

    fireEvent.keyDown(window, { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on backdrop click", () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Demo modal">
        <button type="button">Close</button>
      </Modal>,
    );

    fireEvent.mouseDown(screen.getByRole("dialog", { name: "Demo modal" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("traps Tab inside the modal", async () => {
    render(
      <>
        <button type="button">Outside</button>
        <Modal open onClose={vi.fn()} title="Demo modal">
          <button type="button">First</button>
          <button type="button">Last</button>
        </Modal>
      </>,
    );
    const dialog = screen.getByRole("dialog", { name: "Demo modal" });
    const first = within(dialog).getByRole("button", { name: "First" });
    const last = within(dialog).getByRole("button", { name: "Last" });
    await waitFor(() => expect(document.activeElement).toBe(first));

    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);

    fireEvent.keyDown(window, { key: "Tab" });
    expect(document.activeElement).toBe(first);
  });

  it("restores focus to trigger on close", async () => {
    const user = userEvent.setup();

    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Open modal
          </button>
          <Modal open={open} onClose={() => setOpen(false)} title="Demo modal">
            <button type="button" onClick={() => setOpen(false)}>
              Close
            </button>
          </Modal>
        </>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "Open modal" });
    await user.click(trigger);
    const closeButton = screen.getByRole("button", { name: "Close" });
    await waitFor(() => expect(document.activeElement).toBe(closeButton));

    await user.click(closeButton);

    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("has role=dialog and aria-modal=true", () => {
    render(
      <Modal open onClose={vi.fn()} title="Demo modal">
        <button type="button">Close</button>
      </Modal>,
    );

    const dialog = screen.getByRole("dialog", { name: "Demo modal" });
    const titleId = dialog.getAttribute("aria-labelledby");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(titleId).toBeTruthy();
    expect(titleId ? document.getElementById(titleId)?.textContent : null).toBe("Demo modal");
  });
});
