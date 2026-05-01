/**
 * Tests for the zod-validating `api.parsed` flavour.
 *
 * The parsed path goes through fetch → JSON-decode → schema.safeParse.
 * We mock fetch so the test is purely about the parse layer's
 * behaviour: success returns a typed object; failure throws a
 * structured `ApiError` carrying field-level errors.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { z } from "zod";
import { api, ApiError } from "./api";

const Schema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  count: z.number(),
});

beforeEach(() => {
  vi.restoreAllMocks();
});

function mockFetch(status: number, body: unknown) {
  global.fetch = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: "ok",
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => body,
  } as Response);
}

describe("api.parsed.get", () => {
  it("returns the parsed object on a matching response", async () => {
    mockFetch(200, {
      data: { id: "11111111-1111-1111-1111-111111111111", name: "Cap", count: 5 },
      status: { category: "ok", message: "OK" },
    });
    const out = await api.parsed.get("/whatever", Schema);
    expect(out).toEqual({
      id: "11111111-1111-1111-1111-111111111111",
      name: "Cap",
      count: 5,
    });
  });

  it("throws ApiError(0, schema-mismatch) when fields are missing", async () => {
    mockFetch(200, {
      data: { id: "11111111-1111-1111-1111-111111111111", name: "Cap" }, // missing count
      status: { category: "ok", message: "OK" },
    });
    await expect(api.parsed.get("/whatever", Schema)).rejects.toMatchObject({
      status: 0,
      body: { status: { category: "client_schema_mismatch" } },
    });
  });

  it("throws ApiError(0, schema-mismatch) on wrong type", async () => {
    mockFetch(200, {
      data: { id: "not-a-uuid", name: "Cap", count: "five" },
      status: { category: "ok", message: "OK" },
    });
    await expect(api.parsed.get("/whatever", Schema)).rejects.toBeInstanceOf(ApiError);
  });

  it("strips unknown fields silently (forward-compat)", async () => {
    mockFetch(200, {
      data: {
        id: "11111111-1111-1111-1111-111111111111",
        name: "Cap",
        count: 5,
        future_field: "added by backend",
      },
      status: { category: "ok", message: "OK" },
    });
    const out = await api.parsed.get("/whatever", Schema);
    expect(out).toEqual({
      id: "11111111-1111-1111-1111-111111111111",
      name: "Cap",
      count: 5,
    });
    expect((out as Record<string, unknown>).future_field).toBeUndefined();
  });

  it("propagates HTTP errors as ApiError with the right status", async () => {
    mockFetch(404, {
      data: null,
      status: { category: "not_found", message: "part not found" },
    });
    await expect(api.parsed.get("/whatever", Schema)).rejects.toMatchObject({
      status: 404,
    });
  });
});
