/**
 * Thin fetch wrapper around the backend's `{data, status}` envelope.
 *
 * Two flavours of read methods:
 *
 * - `api.get<T>(path)` / `api.post<T>(...)` etc. — legacy untyped path.
 *   Returns `body.data` cast to `T` with no runtime validation. Used
 *   by call sites that haven't migrated to the parsed flavour yet.
 *
 * - `api.parsed.get(path, schema)` / `parsed.post(...)` etc. — new
 *   zod-validating path. Parses `body.data` against the supplied
 *   schema; throws `ApiError` on schema mismatch with field-level
 *   detail. Adopt for security-sensitive paths and any endpoint where
 *   silent shape drift would be a real bug. See `lib/schemas.ts`.
 *
 * Both flavours share the same `request()` core; only the post-body
 * handling differs.
 */
import type { ZodType } from "zod";

export type ApiOk<T> = { data: T; status: { category: string; message: string } };
export type ApiErr = { data: null; status: { category: string; message: string }; errors?: { field: string; message: string }[] };

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  body: ApiErr | null;
  constructor(status: number, body: ApiErr | null, message: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function rawRequest(path: string, init: RequestInit = {}): Promise<unknown> {
  const headers = new Headers(init.headers || {});
  if (init.body && !(init.body instanceof FormData) && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  const res = await fetch(`${BASE}${path}`, { ...init, headers, credentials: "include" });
  let body: any = null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    body = await res.json();
  }
  if (!res.ok) {
    const msg = body?.status?.message || res.statusText;
    throw new ApiError(res.status, body, msg);
  }
  return body?.data ?? null;
}

// Unsafe legacy cast — kept for back-compat while call sites migrate.
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  return (await rawRequest(path, init)) as T;
}

// Zod-validating path. Throws ApiError(0, null, ...) on schema mismatch.
async function parsedRequest<S extends ZodType>(
  path: string,
  schema: S,
  init: RequestInit = {},
): Promise<S["_output"]> {
  const data = await rawRequest(path, init);
  const result = schema.safeParse(data);
  if (!result.success) {
    // Flatten zod errors into the same shape the backend produces for
    // its 422 responses, so callers can inspect `body.errors`.
    const errors = result.error.issues.map((i) => ({
      field: i.path.join(".") || "<root>",
      message: i.message,
    }));
    throw new ApiError(
      0,
      {
        data: null,
        status: { category: "client_schema_mismatch", message: "API response shape changed" },
        errors,
      },
      `API response did not match schema: ${errors.slice(0, 3).map((e) => `${e.field} (${e.message})`).join("; ")}`,
    );
  }
  return result.data;
}

/**
 * Optional per-call extras the wrappers thread through to fetch():
 *
 * - `signal` lets a caller hand in an AbortController so an unmount or
 *   stale-effect cleanup can cancel an in-flight request (v1 FE HIGH-4).
 *   `useQuery` already provides `signal` via its `queryFn` argument; this
 *   makes the wrappers usable from raw `useEffect` blocks too.
 */
export type ApiOptions = { signal?: AbortSignal };

export const api = {
  // --- legacy untyped flavour ---
  get: <T>(p: string, opts?: ApiOptions) => request<T>(p, { signal: opts?.signal }),
  post: <T>(p: string, body?: any, opts?: ApiOptions) =>
    request<T>(p, {
      method: "POST",
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: opts?.signal,
    }),
  patch: <T>(p: string, body?: any, opts?: ApiOptions) =>
    request<T>(p, {
      method: "PATCH",
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: opts?.signal,
    }),
  delete: <T>(p: string, opts?: ApiOptions) =>
    request<T>(p, { method: "DELETE", signal: opts?.signal }),
  upload: <T>(p: string, form: FormData, opts?: ApiOptions) =>
    request<T>(p, { method: "POST", body: form, signal: opts?.signal }),

  // --- zod-validating flavour ---
  parsed: {
    get: <S extends ZodType>(p: string, schema: S, opts?: ApiOptions) =>
      parsedRequest(p, schema, { signal: opts?.signal }),
    post: <S extends ZodType>(p: string, schema: S, body?: any, opts?: ApiOptions) =>
      parsedRequest(p, schema, {
        method: "POST",
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: opts?.signal,
      }),
    patch: <S extends ZodType>(p: string, schema: S, body?: any, opts?: ApiOptions) =>
      parsedRequest(p, schema, {
        method: "PATCH",
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: opts?.signal,
      }),
    delete: <S extends ZodType>(p: string, schema: S, opts?: ApiOptions) =>
      parsedRequest(p, schema, { method: "DELETE", signal: opts?.signal }),
  },
};
