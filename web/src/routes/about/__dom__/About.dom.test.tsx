/**
 * DOM tests for `/about`.
 *
 * Pinned behaviours:
 *  - both build identifiers render, frontend and backend
 *  - a mismatch between them is called out explicitly, not left for the
 *    reader to diff by eye (the whole reason the page shows both: there
 *    is no staging environment and the auto-deploy can half-apply)
 *  - matching builds produce no warning
 *  - an unset frontend build reads as a development build, never as a
 *    version number
 *  - "Latest changes" renders bounded changelog content
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import About from "../About";

const FRONTEND_SHA = "aaaaaaaaaaaa";
const BACKEND_SHA = "bbbbbbbbbbbb";

const getMock = vi.fn();

vi.mock("@/lib/api", async importOriginal => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, get: (...args: unknown[]) => getMock(...args) },
  };
});

function renderAbout() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/about"]}>
        <About />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getMock.mockReset();
  getMock.mockResolvedValue({ build: BACKEND_SHA });
  vi.stubEnv("VITE_APP_VERSION", FRONTEND_SHA);
  vi.stubEnv("VITE_BUILD_TIME", "2026-09-05T10:00:00Z");
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe("About", () => {
  it("renders the frontend and the backend build side by side", async () => {
    renderAbout();

    expect(screen.getByText("Frontend build")).toBeTruthy();
    expect(screen.getByText("Backend build")).toBeTruthy();
    expect(screen.getByText(FRONTEND_SHA)).toBeTruthy();
    await waitFor(() => expect(screen.getByText(BACKEND_SHA)).toBeTruthy());
  });

  it("calls out a mismatch between the two builds", async () => {
    renderAbout();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/Frontend and backend builds differ/i);
  });

  it("shows no warning when the two builds match", async () => {
    getMock.mockResolvedValue({ build: FRONTEND_SHA });
    renderAbout();

    await waitFor(() => expect(screen.getAllByText(FRONTEND_SHA).length).toBe(2));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("reads an unset frontend build as a development build, not a version", async () => {
    vi.stubEnv("VITE_APP_VERSION", "");
    renderAbout();

    await waitFor(() => expect(screen.getByText(BACKEND_SHA)).toBeTruthy());
    expect(screen.getByText(/development build/i)).toBeTruthy();
    // The frozen 0.1.0 strings in package.json / pyproject.toml must never
    // be presented as this app's version.
    expect(screen.queryByText(/0\.1\.0/)).toBeNull();
    // A mismatch banner would be nonsense when one side is simply unknown.
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("asks the backend through the shared api wrapper, with an abort signal", async () => {
    renderAbout();
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    expect(getMock.mock.calls[0][0]).toBe("/version");
    expect(getMock.mock.calls[0][1]).toHaveProperty("signal");
  });

  it("renders bounded 'Latest changes' from the changelog", () => {
    renderAbout();
    expect(screen.getByText("Latest changes")).toBeTruthy();
    // Three `## ` sections, each rendered as its own card heading.
    expect(screen.getByText(/most recent entries/i)).toBeTruthy();
  });

  it("does not link out to the engineer doc shelf from the changelog", () => {
    // CHANGELOG.md links ADRs and phase docs. Those are engineer-shelf
    // pages that don't exist in the SPA; `resolveDocHref` demotes them to
    // plain text so the label survives and the dead link doesn't.
    renderAbout();
    const hrefs = Array.from(document.querySelectorAll("a")).map(a => a.getAttribute("href") ?? "");
    expect(hrefs.filter(h => h.endsWith(".md"))).toEqual([]);
    expect(hrefs.some(h => /\/(adr|runbooks|phases|api)\//.test(h))).toBe(false);
  });
});
