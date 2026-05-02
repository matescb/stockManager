/**
 * Smoke test for App routing tree.
 *
 * The point of this test is mechanical: render <App /> through a
 * MemoryRouter and assert it doesn't throw. react-router-dom v6 invariants
 * (every direct child of <Routes> must be a <Route> or <Fragment>) are
 * runtime-only — TS sees <Suspense> as a valid ReactNode parent and the
 * existing build/`tsc -b` pipeline never instantiated <App /> outside of
 * `main.tsx`, so the invariant violation that reopened PR-197 shipped
 * green. This test would have caught it.
 *
 * Lives under `__dom__/` so vite.config.ts's environmentMatchGlobs picks
 * jsdom for it.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock api module before App import so AuthProvider's `/auth/me` bootstrap
// resolves to "no session" and we render the Login route deterministically.
// ApiError is referenced from the real module by routes/auth/Login.tsx, so
// keep its shape compatible.
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    body: unknown;
    constructor(status: number, body: unknown, msg = "api error") {
      super(msg);
      this.status = status;
      this.body = body;
    }
  }
  const reject401 = () => Promise.reject(new ApiError(401, {}, "unauth"));
  return {
    ApiError,
    api: {
      get: reject401,
      post: reject401,
      patch: reject401,
      delete: reject401,
      upload: reject401,
      parsed: {
        get: reject401,
        post: reject401,
        patch: reject401,
      },
    },
  };
});

// Sentry's React boundary is fine in jsdom; instrument.ts however does
// real init. Stub the instrument side-effect import so tests don't reach
// out to Sentry.
vi.mock("@/instrument", () => ({}));

import App from "../App";

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderApp(initialEntries: string[] = ["/login"]) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={initialEntries}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("App routing tree", () => {
  it("renders without throwing the react-router invariant on /login", () => {
    expect(() => renderApp(["/login"])).not.toThrow();
  });

  it("renders without throwing on a lazy-section path (/orders)", () => {
    // Without a session the gate redirects to /login; the important
    // thing for the regression is that constructing the route tree
    // succeeds — i.e. <Suspense> is no longer a direct child of <Routes>.
    expect(() => renderApp(["/orders"])).not.toThrow();
  });
});
