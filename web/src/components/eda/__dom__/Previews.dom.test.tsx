/**
 * DOM tests for the KiCad 2D preview components.
 *
 * The previews are server-rendered SVGs shown through an `<img>` (see
 * `SvgPreview`). jsdom does not fetch or decode images, so what is pinned
 * here is the wiring around them: the right backend `.svg` URL, the
 * loading/failed states, the collapse/expand teardown, and the src update
 * when the selection changes. The PreviewBoundary tests guard the error
 * boundary that the 3D model preview still uses.
 */
import React from "react";
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SymbolPreview } from "../SymbolPreview";
import { FootprintPreview } from "../FootprintPreview";
import { PreviewBoundary } from "../PreviewBoundary";

afterEach(() => cleanup());

function img(): HTMLImageElement | null {
  return document.querySelector("[data-testid='svg-preview-img']");
}

describe("SymbolPreview", () => {
  it("renders an <img> at the symbol's SVG render route", () => {
    render(<SymbolPreview symbolId="abc-123" />);
    expect(img()!.getAttribute("src")).toBe(
      "/api/eda/symbols/abc-123/preview.svg",
    );
  });

  it("shows a loading hint until the image loads", () => {
    render(<SymbolPreview symbolId="abc-123" />);
    expect(screen.getByText("Loading preview…")).toBeTruthy();
    fireEvent.load(img()!);
    expect(screen.queryByText("Loading preview…")).toBeNull();
  });

  it("degrades to a plain message when the image fails to load", () => {
    render(<SymbolPreview symbolId="abc-123" />);
    fireEvent.error(img()!);
    expect(screen.getByText("Preview unavailable")).toBeTruthy();
    // The <img> is removed once it has failed, so a broken-image glyph
    // never lingers in the card.
    expect(img()).toBeNull();
  });

  it("updates the src when the selected symbol changes", () => {
    const { rerender } = render(<SymbolPreview symbolId="first" />);
    expect(img()!.getAttribute("src")).toBe("/api/eda/symbols/first/preview.svg");
    rerender(<SymbolPreview symbolId="second" />);
    expect(img()!.getAttribute("src")).toBe(
      "/api/eda/symbols/second/preview.svg",
    );
  });

  it("hides the image when collapsed and restores it when expanded", async () => {
    const user = userEvent.setup();
    render(<SymbolPreview symbolId="abc-123" />);
    expect(img()).not.toBeNull();

    await user.click(screen.getByRole("button", { name: "Hide" }));
    expect(img()).toBeNull();

    await user.click(screen.getByRole("button", { name: "Show" }));
    expect(img()).not.toBeNull();
  });

  it("zooms in from the buttons and resets with Fit", () => {
    render(<SymbolPreview symbolId="abc-123" />);
    // The zoom controls appear only once the image has loaded.
    fireEvent.load(img()!);
    const stage = () =>
      document.querySelector("[data-testid='svg-preview-stage']") as HTMLElement;

    expect(stage().style.transform).toContain("scale(1)");
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(stage().style.transform).toContain("scale(1.25)");
    fireEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expect(stage().style.transform).toContain("scale(1)");
  });
});

describe("FootprintPreview", () => {
  it("renders an <img> at the footprint's SVG render route", () => {
    render(<FootprintPreview footprintId="fp-9" />);
    expect(img()!.getAttribute("src")).toBe(
      "/api/eda/footprints/fp-9/preview.svg",
    );
  });
});

describe("PreviewBoundary", () => {
  it("catches a throwing child and shows the fallback", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    function Boom(): React.ReactElement {
      throw new Error("viewer blew up");
    }

    render(
      <PreviewBoundary resetKey="one">
        <Boom />
      </PreviewBoundary>,
    );

    expect(screen.getByText("Preview unavailable")).toBeTruthy();
  });

  it("recovers when the previewed entry changes", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    function Boom(): React.ReactElement {
      throw new Error("viewer blew up");
    }

    const { rerender } = render(
      <PreviewBoundary resetKey="one">
        <Boom />
      </PreviewBoundary>,
    );
    expect(screen.getByText("Preview unavailable")).toBeTruthy();

    rerender(
      <PreviewBoundary resetKey="two">
        <p>a different symbol</p>
      </PreviewBoundary>,
    );
    expect(screen.getByText("a different symbol")).toBeTruthy();
  });
});
