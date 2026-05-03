/**
 * DOM tests for QueryStateBoundary + InlineQueryError (#245 round 2).
 *
 * Pinned behaviours:
 *  - Renders children when `isError=false`
 *  - Renders error card on a non-401 error
 *  - Renders children (no error card) on 401 — the global auth bus is
 *    already redirecting, a flash of "couldn't load" mid-bounce would
 *    confuse the user (CLAUDE.md: 401 is handled centrally)
 *  - Retry triggers `query.refetch()`
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import QueryStateBoundary, { InlineQueryError, type QueryLike } from "../QueryStateBoundary";

beforeEach(() => {
  cleanup();
});

function makeQuery(overrides: Partial<QueryLike> = {}): QueryLike {
  return {
    isError: false,
    error: null,
    refetch: () => undefined,
    isFetching: false,
    ...overrides,
  };
}

describe("QueryStateBoundary", () => {
  it("renders children when the query has no error", () => {
    render(
      <QueryStateBoundary query={makeQuery()} resourceLabel="parts">
        <div>visible content</div>
      </QueryStateBoundary>,
    );
    expect(screen.getByText("visible content")).toBeDefined();
  });

  it("renders the error card when the query errored (non-401)", () => {
    const q = makeQuery({
      isError: true,
      error: new ApiError(
        500,
        { data: null, status: { category: "server_error", message: "boom" } },
        "boom",
      ),
    });
    render(
      <QueryStateBoundary query={q} resourceLabel="parts">
        <div>hidden content</div>
      </QueryStateBoundary>,
    );
    expect(screen.queryByText("hidden content")).toBeNull();
    expect(screen.getByText(/couldn't load parts/i)).toBeDefined();
    expect(screen.getByRole("button", { name: /retry/i })).toBeDefined();
  });

  it("falls through to children on a 401 (global auth bus handles redirect)", () => {
    const q = makeQuery({
      isError: true,
      error: new ApiError(
        401,
        { data: null, status: { category: "unauthenticated", message: "expired" } },
        "expired",
      ),
    });
    render(
      <QueryStateBoundary query={q} resourceLabel="parts">
        <div>still visible</div>
      </QueryStateBoundary>,
    );
    expect(screen.getByText("still visible")).toBeDefined();
    expect(screen.queryByText(/couldn't load/i)).toBeNull();
  });

  it("Retry button calls query.refetch()", () => {
    const refetch = vi.fn();
    const q = makeQuery({
      isError: true,
      error: new Error("boom"),
      refetch,
    });
    render(
      <QueryStateBoundary query={q} resourceLabel="parts">
        <div />
      </QueryStateBoundary>,
    );
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("disables Retry while isFetching", () => {
    const q = makeQuery({
      isError: true,
      error: new Error("boom"),
      isFetching: true,
    });
    render(
      <QueryStateBoundary query={q} resourceLabel="parts">
        <div />
      </QueryStateBoundary>,
    );
    const btn = screen.getByRole("button", { name: /retrying…/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});

describe("InlineQueryError", () => {
  it("renders nothing when isError=false", () => {
    const { container } = render(
      <InlineQueryError query={makeQuery()} label="storage locations" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders the pill on a non-401 error", () => {
    const q = makeQuery({
      isError: true,
      error: new ApiError(
        500,
        { data: null, status: { category: "server_error", message: "boom" } },
        "boom",
      ),
    });
    render(<InlineQueryError query={q} label="storage locations" />);
    const alert = screen.getByRole("alert");
    expect(alert).toBeDefined();
    expect(alert.textContent?.toLowerCase()).toContain("storage locations");
    expect(screen.getByRole("button", { name: /retry/i })).toBeDefined();
  });

  it("renders nothing on a 401 (global auth bus handles redirect)", () => {
    const q = makeQuery({
      isError: true,
      error: new ApiError(
        401,
        { data: null, status: { category: "unauthenticated", message: "expired" } },
        "expired",
      ),
    });
    const { container } = render(<InlineQueryError query={q} label="storage" />);
    expect(container.firstChild).toBeNull();
  });

  it("Retry button calls query.refetch()", () => {
    const refetch = vi.fn();
    const q = makeQuery({
      isError: true,
      error: new Error("boom"),
      refetch,
    });
    render(<InlineQueryError query={q} label="storage" />);
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("disables Retry while isFetching", () => {
    const q = makeQuery({
      isError: true,
      error: new Error("boom"),
      isFetching: true,
    });
    render(<InlineQueryError query={q} label="storage" />);
    const btn = screen.getByRole("button", { name: /retrying…/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
