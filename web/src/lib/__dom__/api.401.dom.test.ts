/**
 * Tests for `lib/api.ts`'s 401 behaviour (FE-008 / TEST-004).
 *
 * The 401 *redirect* lives in `main.tsx` (a QueryClient onError handler
 * that emits to the auth bus). `lib/api.ts` itself only throws an
 * `ApiError(401, body, msg)` — by design, since the wrapper sits below
 * the auth bus and React-Query layers. This test pins:
 *
 *  1. A 401 response surfaces as `ApiError(status=401)`.
 *  2. The error body is parsed when the server sends JSON, so the
 *     onError handler in main.tsx has the envelope to inspect.
 *  3. A 200 envelope unwraps to `data` cleanly.
 *  4. `credentials: "include"` is set on every request so the session
 *     cookie always rides along.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { api, ApiError } from "../api";

beforeEach(() => {
  vi.restoreAllMocks();
});

function mockFetch(status: number, body: unknown, ok = status >= 200 && status < 300) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok,
    status,
    statusText: ok ? "ok" : "error",
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => body,
  } as unknown as Response);
}

describe("api 401 behaviour", () => {
  it("throws ApiError(status=401) on a 401 response", async () => {
    mockFetch(401, {
      data: null,
      status: { category: "unauthorized", message: "session expired" },
    });
    await expect(api.get("/whatever")).rejects.toBeInstanceOf(ApiError);
    await expect(api.get("/whatever")).rejects.toMatchObject({ status: 401 });
  });

  it("preserves the server error body so the onError handler can inspect it", async () => {
    mockFetch(401, {
      data: null,
      status: { category: "unauthorized", message: "session expired" },
    });
    try {
      await api.get("/whatever");
      throw new Error("expected ApiError");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      const err = e as ApiError;
      expect(err.status).toBe(401);
      expect(err.body?.status?.category).toBe("unauthorized");
      expect(err.body?.status?.message).toBe("session expired");
      expect(err.message).toBe("session expired");
    }
  });

  it("unwraps a 200 envelope to .data", async () => {
    mockFetch(200, {
      data: { id: "p1", name: "Cap" },
      status: { category: "ok", message: "OK" },
    });
    const out = await api.get<{ id: string; name: string }>("/parts/p1");
    expect(out).toEqual({ id: "p1", name: "Cap" });
  });

  it("sends credentials: 'include' on every request", async () => {
    const spy = mockFetch(200, {
      data: null,
      status: { category: "ok", message: "OK" },
    });
    await api.get("/whatever");
    expect(spy).toHaveBeenCalledTimes(1);
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.credentials).toBe("include");
  });

  it("propagates non-401 HTTP errors as ApiError too", async () => {
    mockFetch(500, {
      data: null,
      status: { category: "internal_error", message: "boom" },
    });
    await expect(api.get("/whatever")).rejects.toMatchObject({
      status: 500,
    });
  });
});
