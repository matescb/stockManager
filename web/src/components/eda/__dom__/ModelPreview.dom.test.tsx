/**
 * DOM tests for the 3D model preview card.
 *
 * three.js renders through WebGL and jsdom has none, so `modelRenderer` is
 * mocked wholesale — exactly the stance `Previews.dom.test.tsx` takes with
 * the KiCanvas loader. What is pinned here is the wiring around the viewer,
 * not the viewer:
 *
 *  1. The card hands the renderer the right URL and format — GLB for a
 *     step-derived model, WRL for a native one.
 *  2. The viewer is torn down (its WebGL context released via `dispose`)
 *     when the card is collapsed.
 *  3. A failed load degrades to "3D preview unavailable" rather than
 *     taking the CAD tab with it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mountModelViewer = vi.fn();
const dispose = vi.fn();

vi.mock("../modelRenderer", () => ({
  mountModelViewer: (...args: unknown[]) => mountModelViewer(...args),
}));

import { ModelPreview } from "../ModelPreview";

beforeEach(() => {
  mountModelViewer.mockReset();
  dispose.mockReset();
  mountModelViewer.mockResolvedValue({ dispose });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ModelPreview", () => {
  it("mounts the renderer with the GLB url and format for a step model", async () => {
    render(
      <ModelPreview
        src="/api/eda/datafiles/abc/preview.glb"
        format="glb"
        title="cube (STEP)"
      />,
    );

    await waitFor(() => expect(mountModelViewer).toHaveBeenCalled());
    const [host, opts] = mountModelViewer.mock.calls[0];
    expect(host).toBeInstanceOf(HTMLElement);
    expect(opts).toMatchObject({ src: "/api/eda/datafiles/abc/preview.glb", format: "glb" });
  });

  it("mounts the renderer with wrl format for a native mesh", async () => {
    render(
      <ModelPreview
        src="/api/eda/files/ws-1/deadbeef.wrl"
        format="wrl"
        title="part (WRL)"
      />,
    );

    await waitFor(() => expect(mountModelViewer).toHaveBeenCalled());
    expect(mountModelViewer.mock.calls[0][1]).toMatchObject({
      src: "/api/eda/files/ws-1/deadbeef.wrl",
      format: "wrl",
    });
  });

  it("disposes the viewer when the card is collapsed", async () => {
    const user = userEvent.setup();
    render(
      <ModelPreview src="/api/eda/datafiles/abc/preview.glb" format="glb" title="cube" />,
    );
    await waitFor(() => expect(mountModelViewer).toHaveBeenCalled());
    // Let the mount promise settle so the handle is stored before teardown.
    await waitFor(() => expect(screen.queryByText("Loading 3D preview…")).toBeNull());

    await user.click(screen.getByRole("button", { name: "Hide" }));
    await waitFor(() => expect(dispose).toHaveBeenCalled());
  });

  it("degrades to a plain message when the model fails to load", async () => {
    mountModelViewer.mockRejectedValue(new Error("bad step"));
    render(
      <ModelPreview src="/api/eda/datafiles/abc/preview.glb" format="glb" title="cube" />,
    );

    expect(await screen.findByText("3D preview unavailable")).toBeTruthy();
  });
});
