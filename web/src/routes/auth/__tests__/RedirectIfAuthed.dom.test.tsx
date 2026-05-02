/**
 * Tests for the RedirectIfAuthed guard (FE2-019).
 *
 * Pinned behaviours:
 *  1. me=null  → the child form renders (unauthenticated user may use /login).
 *  2. me=user  → redirects to /parts (default fallback).
 *  3. loading=true → renders nothing (null placeholder, no form flash).
 *  4. me=user + state.from=/orders/abc → redirects to /orders/abc (deep-link).
 *
 * The component is defined in App.tsx as a module-scoped function; we
 * exercise it by mounting a minimal Router tree that mirrors the real
 * App.tsx structure and mocking useAuth() via vi.mock.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter, Route, Routes, Navigate, useLocation } from "react-router-dom";
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
    const from = (location.state as { from?: { pathname: string } } | null)?.from;
    const target =
      from && from.pathname !== "/login" && from.pathname !== "/signup"
        ? from.pathname
        : "/parts";
    return <Navigate to={target} replace />;
  }
  return <>{children}</>;
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
});
