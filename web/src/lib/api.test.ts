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
import { api, ApiError, categoryToUserMessage } from "./api";

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

describe("categoryToUserMessage", () => {
  it("maps unauthenticated to session expired message", () => {
    expect(categoryToUserMessage("unauthenticated")).toBe(
      "Session expired. Please sign in again.",
    );
  });

  it("maps forbidden to permission message", () => {
    expect(categoryToUserMessage("forbidden")).toBe(
      "You don't have permission to do that.",
    );
  });

  it("maps not_found to not found message", () => {
    expect(categoryToUserMessage("not_found")).toBe("Not found.");
  });

  it("maps conflict to duplicate message", () => {
    expect(categoryToUserMessage("conflict")).toBe(
      "That's a duplicate or conflicts with existing data.",
    );
  });

  it("maps validation_error to form check message", () => {
    expect(categoryToUserMessage("validation_error")).toBe(
      "Some fields don't look right. Check the form and retry.",
    );
  });

  it("maps server_error to generic fallback", () => {
    expect(categoryToUserMessage("server_error")).toBe(
      "Something went wrong. Try again, or refresh.",
    );
  });

  it("maps unknown category to generic fallback", () => {
    expect(categoryToUserMessage("some_future_category")).toBe(
      "Something went wrong. Try again, or refresh.",
    );
  });

  it("maps null/undefined to generic fallback", () => {
    expect(categoryToUserMessage(null)).toBe(
      "Something went wrong. Try again, or refresh.",
    );
    expect(categoryToUserMessage(undefined)).toBe(
      "Something went wrong. Try again, or refresh.",
    );
  });
});

describe("ApiError.userMessage", () => {
  it("populates userMessage from body.status.category", () => {
    const err = new ApiError(
      404,
      {
        data: null,
        status: { category: "not_found", message: "DB says nope" },
        code: "purchase_plan_not_found",
      },
      "DB says nope",
    );
    expect(err.message).toBe("DB says nope");
    expect(err.userMessage).toBe("Not found.");
    expect(err.code).toBe("purchase_plan_not_found");
  });

  it("leaves code undefined when the envelope has no structured code", () => {
    const err = new ApiError(
      409,
      { data: null, status: { category: "conflict", message: "dup" } },
      "dup",
    );
    expect(err.code).toBeUndefined();
  });

  it("populates userMessage as generic fallback when body is null", () => {
    const err = new ApiError(500, null, "Internal Server Error");
    expect(err.message).toBe("Internal Server Error");
    expect(err.userMessage).toBe("Something went wrong. Try again, or refresh.");
  });

  it("raw message is preserved separately from userMessage", () => {
    const raw = "sqlalchemy.exc.IntegrityError: DETAIL: Key (mpn)=(ABC) already exists.";
    const err = new ApiError(
      409,
      { data: null, status: { category: "conflict", message: raw } },
      raw,
    );
    expect(err.message).toBe(raw);
    expect(err.userMessage).toBe(
      "That's a duplicate or conflicts with existing data.",
    );
    expect(err.userMessage).not.toContain("sqlalchemy");
  });

  it("HTTP 404 ApiError thrown by api.parsed carries correct userMessage", async () => {
    mockFetch(404, {
      data: null,
      status: { category: "not_found", message: "part not found" },
    });
    let caught: ApiError | null = null;
    try {
      await api.parsed.get("/whatever", Schema);
    } catch (e) {
      if (e instanceof ApiError) caught = e;
    }
    expect(caught).not.toBeNull();
    expect(caught!.userMessage).toBe("Not found.");
    expect(caught!.message).toBe("part not found");
  });
});
