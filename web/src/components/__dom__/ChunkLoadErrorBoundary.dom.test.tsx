/**
 * DOM tests for ChunkLoadErrorBoundary (FE2-022).
 *
 * Runs against jsdom (matched by the `__dom__/` glob in vite.config.ts).
 *
 * Pinned behaviours:
 *  1. A child that throws a ChunkLoadError triggers window.location.reload()
 *     exactly once, and sessionStorage is flagged for that pathname.
 *  2. When the sessionStorage flag is already set (second attempt), the
 *     boundary does NOT call reload again and instead shows the retry banner.
 *  3. A non-chunk error is re-thrown so the outer boundary can capture it.
 *  4. The Vite dynamic-import message variant is also recognised.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ChunkLoadErrorBoundary } from "../ChunkLoadErrorBoundary";

// Suppress React's console.error noise from intentional throws in tests.
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

// Helper: a component that throws the given error on render.
function Thrower({ error }: { error: Error }): React.ReactElement {
  throw error;
}

function makeChunkError(name = "ChunkLoadError") {
  const err = new Error("Loading chunk 42 failed.");
  err.name = name;
  return err;
}

function makeNonChunkError() {
  return new TypeError("Something totally unrelated blew up");
}

// Outer boundary that absorbs re-thrown errors so the test process
// doesn't crash when ChunkLoadErrorBoundary re-throws non-chunk errors.
class SafeOuter extends React.Component<
  { children: React.ReactNode },
  { caught: boolean }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { caught: false };
  }
  static getDerivedStateFromError() {
    return { caught: true };
  }
  render() {
    if (this.state.caught) return <div data-testid="outer-caught" />;
    return this.props.children;
  }
}

function renderBoundary(child: React.ReactNode) {
  return render(
    <SafeOuter>
      <ChunkLoadErrorBoundary>{child}</ChunkLoadErrorBoundary>
    </SafeOuter>
  );
}

describe("ChunkLoadErrorBoundary", () => {
  it("calls window.location.reload() once on the first ChunkLoadError", () => {
    const reloadSpy = vi.fn();
    // jsdom's window.location.reload is a no-op; stub it so we can spy.
    vi.stubGlobal("location", {
      pathname: "/orders",
      reload: reloadSpy,
    });

    renderBoundary(<Thrower error={makeChunkError()} />);

    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem("chunkReloadAttempt:/orders")).toBe("1");

    vi.unstubAllGlobals();
  });

  it("shows the retry banner on the second ChunkLoadError (no further reload)", () => {
    const reloadSpy = vi.fn();
    vi.stubGlobal("location", {
      pathname: "/reports",
      reload: reloadSpy,
    });

    // Pre-set the flag to simulate the page having already been reloaded.
    sessionStorage.setItem("chunkReloadAttempt:/reports", "1");

    renderBoundary(<Thrower error={makeChunkError()} />);

    expect(reloadSpy).not.toHaveBeenCalled();
    expect(screen.getByText(/failed to load/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();

    vi.unstubAllGlobals();
  });

  it("re-throws non-chunk errors so the outer boundary can catch them", () => {
    vi.stubGlobal("location", {
      pathname: "/parts",
      reload: vi.fn(),
    });

    const { container } = renderBoundary(<Thrower error={makeNonChunkError()} />);

    // The outer SafeOuter boundary should have caught the re-thrown error.
    const caught = container.querySelector('[data-testid="outer-caught"]');
    expect(caught).not.toBeNull();

    vi.unstubAllGlobals();
  });

  it("recognises the Vite dynamic-import message variant as a chunk error", () => {
    const reloadSpy = vi.fn();
    vi.stubGlobal("location", {
      pathname: "/builds",
      reload: reloadSpy,
    });

    const viteErr = new Error(
      "Failed to fetch dynamically imported module: /assets/BuildsList-abc123.js"
    );
    viteErr.name = "Error";

    renderBoundary(<Thrower error={viteErr} />);

    expect(reloadSpy).toHaveBeenCalledTimes(1);

    vi.unstubAllGlobals();
  });
});
