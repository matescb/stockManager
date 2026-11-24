// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import EntityHeader from "../EntityHeader";
import ImageLightbox from "../ImageLightbox";

const IMG = "/api/parts/assets/ws-1/abc.png";

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
  document.body.style.paddingRight = "";
});

function renderHeader(imageUrl: string | null = IMG) {
  return render(<EntityHeader title="LM358" imageUrl={imageUrl} imageTitle="LM358DR" />);
}

describe("EntityHeader thumbnail → ImageLightbox", () => {
  it("opens an in-page dialog instead of linking to a new tab", async () => {
    renderHeader();
    const trigger = screen.getByRole("button", { name: "View full image" });
    expect(trigger.closest("a")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();

    await userEvent.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "LM358DR" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(within(dialog).getByRole("img", { name: "LM358DR" }).getAttribute("src")).toBe(IMG);
    const original = within(dialog).getByRole("link", { name: /open original/i });
    expect(original.getAttribute("href")).toBe(IMG);
    expect(original.getAttribute("target")).toBe("_blank");
    await waitFor(() =>
      expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Close image" })),
    );
  });

  it("closes on Escape and returns focus to the thumbnail", async () => {
    renderHeader();
    const trigger = screen.getByRole("button", { name: "View full image" });
    trigger.focus();
    await userEvent.click(trigger);
    expect(screen.getByRole("dialog")).toBeTruthy();

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("closes on backdrop click and on the close button", async () => {
    renderHeader();
    const trigger = screen.getByRole("button", { name: "View full image" });

    await userEvent.click(trigger);
    fireEvent.mouseDown(screen.getByRole("dialog"));
    expect(screen.queryByRole("dialog")).toBeNull();

    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole("button", { name: "Close image" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("locks body scroll while open and restores it on close", async () => {
    document.body.style.overflow = "auto";
    renderHeader();

    await userEvent.click(screen.getByRole("button", { name: "View full image" }));
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(document.body.style.overflow).toBe("auto");
  });

  it("renders no thumbnail for an unsafe image URL", () => {
    renderHeader("javascript:alert(1)");
    expect(screen.queryByRole("button", { name: "View full image" })).toBeNull();
  });
});

describe("ImageLightbox", () => {
  it("renders nothing when the src is unsafe even if open", () => {
    render(<ImageLightbox open onClose={() => {}} src="javascript:alert(1)" title="x" />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("hides the open-original link when originalHref is null", () => {
    render(<ImageLightbox open onClose={() => {}} src={IMG} title="Photo" originalHref={null} />);
    expect(screen.getByRole("dialog", { name: "Photo" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /open original/i })).toBeNull();
  });

  it("shows a fallback when the image fails to load", () => {
    render(<ImageLightbox open onClose={() => {}} src={IMG} title="Photo" />);
    fireEvent.error(screen.getByRole("img", { name: "Photo" }));
    expect(screen.getByText("Image failed to load.")).toBeTruthy();
  });
});
