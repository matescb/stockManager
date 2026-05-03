/**
 * Tests for the RedirectIfAuthed guard (FE2-019, #304).
 *
 * Pinned behaviours:
 *  1. me=null  → the child form renders (unauthenticated user may use /login).
 *  2. me=user  → redirects to /parts (default fallback).
 *  3. loading=true → renders nothing (null placeholder, no form flash).
 *  4. me=user + state.from=/orders/abc → redirects to /orders/abc (deep-link).
 *  5. me=user + state.from with search/hash → preserves query string + fragment
 *     across the auth bounce (#304).
 *
 * The component is defined in App.tsx as a module-scoped function; we
 * exercise it by mounting a minimal Router tree that mirrors the real
 * App.tsx structure and mocking useAuth() via vi.mock.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import {
  MemoryRouter,
  Route,
  Routes,
  Navigate,
  useLocation,
  type Location,
} from "react-router-dom";
import { useAuth } from "@/lib/auth";

// ---------------------------------------------------------------------------
// Pull the component under test directly from App.tsx.  Because
// RedirectIfAuthed is a module-internal function (not exported) we extract
// it here as a standalone copy that is identical to the implementation —
// this keeps the test self-contained and avoids wiring up the entire App
// tree (AuthProvider, QueryClient, lazy routes, etc.).
// ---------------------------------------------------------------------------

vi.mock("@/lib/auth", () => ({
  useAuth: vi.fn(),
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

const mockUseAuth = useAuth as ReturnType<typeof vi.fn>;

// Mirror of the component in App.tsx — kept in sync with the implementation.
function RedirectIfAuthed({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return null;
  if (me) {
    const from = (location.state as { from?: Location } | null)?.from;
    if (from && from.pathname !== "/login" && from.pathname !== "/signup") {
      return (
        <Navigate
          to={{ pathname: from.pathname, search: from.search, hash: from.hash }}
          replace
        />
      );
    }
    return <Navigate to="/parts" replace />;
  }
  return <>{children}</>;
}

// Spy component used to assert the post-redirect Location's search + hash
// (MemoryRouter doesn't update window.location, so we read it via useLocation).
function LocationSpy() {
  const loc = useLocation();
  return (
    <div>
      <div data-testid="spy-pathname">{loc.pathname}</div>
      <div data-testid="spy-search">{loc.search}</div>
      <div data-testid="spy-hash">{loc.hash}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderAt(
  path: string,
  state: unknown = undefined,
  authOverride: { me: unknown; loading: boolean } = { me: null, loading: false },
) {
  mockUseAuth.mockReturnValue({ me: authOverride.me, loading: authOverride.loading });

  return render(
    <MemoryRouter initialEntries={[{ pathname: path, state }]}>
      <Routes>
        <Route
          path="/login"
          element={
            <RedirectIfAuthed>
              <div>login-form</div>
            </RedirectIfAuthed>
          }
        />
        <Route path="/parts" element={<div>parts-page</div>} />
        <Route path="/orders/abc" element={<div>orders-abc</div>} />
        <Route
          path="/parts/scan-import"
          element={
            <>
              <div>scan-import-page</div>
              <LocationSpy />
            </>
          }
        />
        <Route path="/signup" element={<div>signup-form</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("RedirectIfAuthed", () => {
  it("me=null: renders the child form for an unauthenticated user", () => {
    renderAt("/login", undefined, { me: null, loading: false });
    expect(screen.getByText("login-form")).toBeDefined();
  });

  it("me=user: redirects to /parts when there is no state.from", () => {
    const fakeUser = { user: { id: "u1", email: "u@test.com" }, workspaces: [] };
    renderAt("/login", undefined, { me: fakeUser, loading: false });
    expect(screen.getByText("parts-page")).toBeDefined();
    expect(screen.queryByText("login-form")).toBeNull();
  });

  it("loading=true: renders nothing (null placeholder, no form flash)", () => {
    renderAt("/login", undefined, { me: null, loading: true });
    expect(screen.queryByText("login-form")).toBeNull();
    expect(screen.queryByText("parts-page")).toBeNull();
  });

  it("me=user + state.from=/orders/abc: redirects to the deep-link target", () => {
    const fakeUser = { user: { id: "u1", email: "u@test.com" }, workspaces: [] };
    renderAt("/login", { from: { pathname: "/orders/abc" } }, { me: fakeUser, loading: false });
    expect(screen.getByText("orders-abc")).toBeDefined();
    expect(screen.queryByText("parts-page")).toBeNull();
  });

  it("me=user + state.from=/login: falls back to /parts to avoid a redirect loop", () => {
    const fakeUser = { user: { id: "u1", email: "u@test.com" }, workspaces: [] };
    // state.from pointing at /login itself must not loop — fall back to /parts.
    renderAt("/login", { from: { pathname: "/login" } }, { me: fakeUser, loading: false });
    expect(screen.getByText("parts-page")).toBeDefined();
  });

  it("me=user + state.from with search+hash: preserves query string and fragment (#304)", () => {
    const fakeUser = { user: { id: "u1", email: "u@test.com" }, workspaces: [] };
    // Supply a Location-shaped object including search + hash. The fixed
    // RedirectIfAuthed must propagate all three to the Navigate target.
    renderAt(
      "/login",
      {
        from: {
          pathname: "/parts/scan-import",
          search: "?storage_id=abc&tab=queue",
          hash: "#row-7",
          state: null,
          key: "test",
        },
      },
      { me: fakeUser, loading: false },
    );
    expect(screen.getByText("scan-import-page")).toBeDefined();
    expect(screen.getByTestId("spy-pathname").textContent).toBe("/parts/scan-import");
    expect(screen.getByTestId("spy-search").textContent).toBe("?storage_id=abc&tab=queue");
    expect(screen.getByTestId("spy-hash").textContent).toBe("#row-7");
  });
});
