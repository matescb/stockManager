/**
 * DOM tests for the in-app manual viewer.
 *
 * Pinned behaviours:
 *  - a real `docs/user/` page renders its title and prose
 *  - relative `.md` links become in-app `/help/*` routes (they would 404
 *    otherwise — the shelf is written for GitHub, where bare sibling
 *    filenames resolve)
 *  - links that leave the end-user shelf are demoted to plain text, per
 *    the doc-shelf boundary in CLAUDE.md
 *  - screenshot placeholders don't leak into the rendered page
 *  - an unknown slug gets an empty state, not a blank card
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import HelpPage from "../HelpPage";
import HelpIndex from "../HelpIndex";

// jsdom has no layout engine, so window.scrollTo is a "Not implemented"
// stub that spams the virtual console. HelpPage calls it to reset scroll
// on a rail navigation; stub it out rather than lose the behaviour.
beforeEach(() => {
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

function renderHelp(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/help" element={<HelpIndex />} />
        <Route path="/help/:slug" element={<HelpPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("HelpPage", () => {
  it("renders a bundled manual page", () => {
    renderHelp("/help/stock");
    // docs/user/stock.md's H1, lifted out of the body by parseDoc.
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBeTruthy();
    expect(document.body.textContent).toMatch(/stock/i);
  });

  it("rewrites relative .md links to in-app help routes", () => {
    renderHelp("/help/stock");
    // stock.md links to scan-import.md and orders.md.
    const hrefs = Array.from(document.querySelectorAll("a")).map(a => a.getAttribute("href"));
    expect(hrefs).toContain("/help/scan-import");
    expect(hrefs).toContain("/help/orders");
    // Nothing still points at a raw markdown file.
    expect(hrefs.filter(h => h?.endsWith(".md"))).toEqual([]);
  });

  it("keeps the cross-page anchor on the one link that carries it", () => {
    renderHelp("/help/projects-and-bom");
    const hrefs = Array.from(document.querySelectorAll("a")).map(a => a.getAttribute("href"));
    expect(hrefs).toContain("/help/parts#pick-a-part-type");
  });

  it("does not render screenshot placeholders", () => {
    renderHelp("/help/getting-started");
    expect(screen.queryByText(/Screenshot:/i)).toBeNull();
  });

  it("does not render the docs-shelf `Audience:` convention line", () => {
    renderHelp("/help/parts");
    expect(screen.queryByText(/Audience: end user/i)).toBeNull();
  });

  it("empty-states an unknown slug", () => {
    renderHelp("/help/not-a-page");
    expect(screen.getByText(/No such help page/i)).toBeTruthy();
  });
});

describe("HelpIndex", () => {
  it("lists every manual page as an in-app link", () => {
    renderHelp("/help");
    const hrefs = Array.from(document.querySelectorAll("a")).map(a => a.getAttribute("href"));
    expect(hrefs).toContain("/help/getting-started");
    expect(hrefs).toContain("/help/kicad");
  });

  it("demotes the engineer-docs link to plain text", () => {
    // docs/user/README.md:5 — "…you want [`docs/`](../README.md) instead."
    // Correct on GitHub, out of bounds in the app.
    renderHelp("/help");
    const label = screen.getByText("docs/");
    expect(label.closest("a")).toBeNull();

    const hrefs = Array.from(document.querySelectorAll("a")).map(a => a.getAttribute("href") ?? "");
    expect(hrefs.some(h => h.includes(".."))).toBe(false);
    expect(hrefs.some(h => /\/(adr|runbooks|phases|api)\//.test(h))).toBe(false);
  });
});
