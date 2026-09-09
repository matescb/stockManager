/**
 * DOM tests for the collapsible app sidebar.
 *
 * Three promises, none of them visible in a type signature:
 *
 *  1. The toggle is a real button with `aria-expanded` and a name that
 *     says what pressing it will do.
 *  2. **Collapsed is escapable.** The re-open control and every nav
 *     destination keep an accessible name in the icon rail, so neither a
 *     pointer user nor a screen-reader user is stranded.
 *  3. The choice survives a remount, and a corrupt stored value does not
 *     strand the user collapsed.
 *
 * Collapse is expressed in `lg:`-prefixed Tailwind classes, which jsdom
 * never compiles — so the width assertion checks the class is applied
 * rather than a computed style. The behavioural contract (`aria-expanded`,
 * accessible names) is asserted directly.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { panelCollapseStorageKey } from "@/lib/usePanelCollapse";

vi.mock("@/instrument", () => ({}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    me: { user: { name: "Ada" }, workspaces: [{ id: "ws-1", name: "Bench" }] },
    workspaceId: "ws-1",
    switchWorkspace: vi.fn(),
    logout: vi.fn(),
  }),
}));

// The palette opens on ⌘K and fetches on every keystroke; the sidebar
// only renders the trigger button, which lives in AppShell itself.
vi.mock("@/components/CommandPalette", () => ({ default: () => null }));

// `useConfirm` throws outside its provider and nothing here confirms.
vi.mock("@/components/ConfirmDialog", () => ({
  useConfirm: () => vi.fn(async () => false),
}));

// `useTheme` likewise throws outside <ThemeProvider>; the toggle is header
// chrome that has nothing to do with the sidebar.
vi.mock("@/components/ThemeToggle", () => ({ default: () => null }));

import AppShell from "../AppShell";

const SIDEBAR_KEY = panelCollapseStorageKey("sidebar", "ws-1");

function renderShell() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/parts"]}>
        <AppShell>
          <div>page body</div>
        </AppShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The `<aside>` shell — an `aside` is a `complementary` landmark. */
function sidebar(): HTMLElement {
  return screen.getByRole("complementary", { name: "Primary navigation" });
}

const COLLAPSED_WIDTH_CLASS = "lg:w-16";

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("workspaceId", "ws-1");
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("app sidebar collapse", () => {
  it("starts expanded, with a toggle that says what it will do", () => {
    renderShell();

    const toggle = screen.getByRole("button", { name: "Collapse sidebar" });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.getAttribute("aria-controls")).toBe("app-sidebar");
    expect(sidebar().className).not.toContain(COLLAPSED_WIDTH_CLASS);
  });

  it("collapsing narrows the sidebar and flips the toggle", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));

    const toggle = screen.getByRole("button", { name: "Expand sidebar" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(sidebar().className).toContain(COLLAPSED_WIDTH_CLASS);
  });

  it("expanding again restores the full sidebar", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    await user.click(screen.getByRole("button", { name: "Expand sidebar" }));

    expect(
      screen.getByRole("button", { name: "Collapse sidebar" }).getAttribute("aria-expanded"),
    ).toBe("true");
    expect(sidebar().className).not.toContain(COLLAPSED_WIDTH_CLASS);
  });

  it("the collapsed rail is still navigable — every link keeps a name", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));

    const nav = sidebar();
    for (const label of ["Parts", "Storage", "Projects", "Orders", "Builds", "Reports", "Alerts"]) {
      expect(within(nav).getByRole("link", { name: label })).toBeTruthy();
    }
    // The footer links and the search trigger live outside <nav>.
    for (const label of ["Settings", "Help", "About"]) {
      expect(screen.getByRole("link", { name: label })).toBeTruthy();
    }
    expect(screen.getByRole("button", { name: "Search…" })).toBeTruthy();
  });

  it("the collapsed state is remembered across a remount", async () => {
    const user = userEvent.setup();
    const first = renderShell();

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    first.unmount();

    renderShell();
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeTruthy();
    expect(sidebar().className).toContain(COLLAPSED_WIDTH_CLASS);
  });

  it("a corrupt stored value falls back to expanded", () => {
    localStorage.setItem(SIDEBAR_KEY, "{not json");

    renderShell();

    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toBeTruthy();
    expect(sidebar().className).not.toContain(COLLAPSED_WIDTH_CLASS);
  });

  it("the mobile drawer is untouched by the collapse state", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));

    // Below `lg` the drawer is opened by the header button and closed by
    // the one inside it; neither is conditional on `collapsed`, and every
    // collapse class is `lg:`-prefixed so the drawer keeps its full width.
    expect(screen.getByRole("button", { name: "Open menu" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Close menu" })).toBeTruthy();
    expect(sidebar().className).toContain("w-60");
  });
});
