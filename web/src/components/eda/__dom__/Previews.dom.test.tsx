/**
 * DOM tests for the KiCad 2D preview components.
 *
 * Runs against jsdom (matched by the `__dom__/` glob in vite.config.ts),
 * which is why the KiCanvas loader is mocked rather than exercised:
 * KiCanvas renders through WebGL and jsdom has no canvas or WebGL. There
 * is nothing to gain from pretending otherwise, so what is pinned here is
 * the wiring around the viewer, not the viewer:
 *
 *  1. Each component points `<kicanvas-embed>` at the right backend URL —
 *     including the `.kicad_sch` / `.kicad_pcb` suffix, which KiCanvas
 *     uses to type the document and which is therefore load-bearing.
 *  2. The viewer only exists while the panel is expanded, and collapsing
 *     it tears the element down (that is how the WebGL context is freed).
 *  3. A failed bundle load degrades to "Preview unavailable".
 *  4. A throwing child is caught by PreviewBoundary, so one unparseable
 *     symbol cannot take the CAD tab with it.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const loadKicanvas = vi.fn<() => Promise<void>>();

vi.mock("../kicanvas", () => ({
  loadKicanvas: () => loadKicanvas(),
  KICANVAS_SRC: "/kicanvas/kicanvas.js",
}));

import { SymbolPreview } from "../SymbolPreview";
import { FootprintPreview } from "../FootprintPreview";
import { PreviewBoundary } from "../PreviewBoundary";

beforeEach(() => {
  loadKicanvas.mockReset();
  loadKicanvas.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function embed(): Element | null {
  return document.querySelector("kicanvas-embed");
}

describe("SymbolPreview", () => {
  it("embeds the synthetic schematic route for the symbol", async () => {
    render(<SymbolPreview symbolId="abc-123" />);

    await waitFor(() => expect(embed()).not.toBeNull());
    expect(embed()!.getAttribute("src")).toBe(
      "/api/eda/symbols/abc-123/preview.kicad_sch",
    );
  });

  it("suppresses every control that would need the removed webfont", async () => {
    render(<SymbolPreview symbolId="abc-123" />);

    await waitFor(() => expect(embed()).not.toBeNull());
    expect(embed()!.getAttribute("controls")).toBe("basic");
    const list = embed()!.getAttribute("controlslist") ?? "";
    expect(list).toContain("nooverlay");
    // download and flip are the only controls KiCanvas draws as Material
    // Symbols ligature text, and the vendored bundle no longer loads that
    // font. Dropping either assertion puts the literal word "download" or
    // "flip" on screen where an icon belongs.
    expect(list).toContain("nodownload");
    expect(list).toContain("noflipview");
  });

  it("rebuilds the element when the selected symbol changes", async () => {
    const { rerender } = render(<SymbolPreview symbolId="first" />);
    await waitFor(() => expect(embed()).not.toBeNull());
    const first = embed();

    rerender(<SymbolPreview symbolId="second" />);
    await waitFor(() =>
      expect(embed()!.getAttribute("src")).toBe(
        "/api/eda/symbols/second/preview.kicad_sch",
      ),
    );
    expect(embed()).not.toBe(first);
  });

  it("tears the viewer down when collapsed", async () => {
    const user = userEvent.setup();
    render(<SymbolPreview symbolId="abc-123" />);
    await waitFor(() => expect(embed()).not.toBeNull());

    await user.click(screen.getByRole("button", { name: "Hide" }));
    expect(embed()).toBeNull();

    await user.click(screen.getByRole("button", { name: "Show" }));
    await waitFor(() => expect(embed()).not.toBeNull());
  });

  it("degrades to a plain message when the bundle fails to load", async () => {
    loadKicanvas.mockRejectedValue(new Error("offline"));
    render(<SymbolPreview symbolId="abc-123" />);

    expect(await screen.findByText("Preview unavailable")).toBeTruthy();
    expect(embed()).toBeNull();
  });
});

describe("FootprintPreview", () => {
  it("embeds the synthetic board route for the footprint", async () => {
    render(<FootprintPreview footprintId="fp-9" />);

    await waitFor(() => expect(embed()).not.toBeNull());
    expect(embed()!.getAttribute("src")).toBe(
      "/api/eda/footprints/fp-9/preview.kicad_pcb",
    );
  });
});

describe("PreviewBoundary", () => {
  it("catches a throwing child and shows the fallback", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    function Boom(): React.ReactElement {
      throw new Error("kicanvas blew up");
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
      throw new Error("kicanvas blew up");
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
