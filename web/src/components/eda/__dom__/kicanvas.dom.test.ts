/**
 * DOM tests for the KiCanvas script loader.
 *
 * The components' own tests mock this module, so it needs its own
 * coverage — and it is the piece with real logic: a module-scoped memo
 * and a failure path that has to leave the DOM clean enough to retry.
 *
 * jsdom does not fetch `<script src>`, which is convenient: the load and
 * error events are dispatched by hand, so both branches are reachable
 * without a network. Each test re-imports the module through
 * `vi.resetModules()` because the memo lives at module scope.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

async function freshLoader() {
  vi.resetModules();
  return await import("../kicanvas");
}

function scripts(): HTMLScriptElement[] {
  return Array.from(document.querySelectorAll("script[data-kicanvas]"));
}

beforeEach(() => {
  document.head.replaceChildren();
});

afterEach(() => {
  document.head.replaceChildren();
  vi.restoreAllMocks();
});

describe("loadKicanvas", () => {
  it("appends one module script pointing at the vendored bundle", async () => {
    const { loadKicanvas, KICANVAS_SRC } = await freshLoader();
    void loadKicanvas();

    expect(scripts()).toHaveLength(1);
    expect(scripts()[0].type).toBe("module");
    expect(scripts()[0].getAttribute("src")).toBe(KICANVAS_SRC);
    expect(KICANVAS_SRC).toContain("kicanvas/kicanvas.js");
  });

  it("resolves when the bundle loads", async () => {
    const { loadKicanvas } = await freshLoader();
    const promise = loadKicanvas();

    scripts()[0].dispatchEvent(new Event("load"));

    await expect(promise).resolves.toBeUndefined();
  });

  it("fetches the bundle once however many previews mount", async () => {
    const { loadKicanvas } = await freshLoader();
    const first = loadKicanvas();
    const second = loadKicanvas();

    expect(first).toBe(second);
    expect(scripts()).toHaveLength(1);
  });

  it("rejects and clears the dead script so a retry starts clean", async () => {
    const { loadKicanvas } = await freshLoader();
    const promise = loadKicanvas();

    scripts()[0].dispatchEvent(new Event("error"));

    await expect(promise).rejects.toThrow(/failed to load/i);
    // The failed tag is gone: a script that has already errored never
    // fires again, so reusing it would hang the retry rather than fail it.
    expect(scripts()).toHaveLength(0);

    // ...and the memo was dropped, so remounting genuinely retries.
    const retry = loadKicanvas();
    expect(scripts()).toHaveLength(1);
    scripts()[0].dispatchEvent(new Event("load"));
    await expect(retry).resolves.toBeUndefined();
  });

  it("short-circuits when the element is already defined", async () => {
    const { loadKicanvas } = await freshLoader();
    vi.spyOn(customElements, "get").mockReturnValue(
      class extends HTMLElement {},
    );

    await expect(loadKicanvas()).resolves.toBeUndefined();
    expect(scripts()).toHaveLength(0);
  });
});
