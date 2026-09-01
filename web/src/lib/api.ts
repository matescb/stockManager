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
export type ApiErr = {
  data: null;
  status: { category: string; message: string };
  code?: string;
  errors?: { field: string; message: string }[];
  request_id?: string;
  retry_after_seconds?: number;
};

const BASE = "/api";

/**
 * Map a backend `status.category` value to a safe, human-readable string
 * that can be shown directly in the UI without leaking server internals.
 * The raw `message` is kept on `ApiError.message` for Sentry / console.
 */
export function categoryToUserMessage(category: string | undefined | null): string {
  switch (category) {
    case "unauthenticated":
      return "Session expired. Please sign in again.";
    case "forbidden":
      return "You don't have permission to do that.";
    case "not_found":
      return "Not found.";
    case "conflict":
      return "That's a duplicate or conflicts with existing data.";
    case "validation_error":
      return "Some fields don't look right. Check the form and retry.";
    default:
      return "Something went wrong. Try again, or refresh.";
  }
}

function authCodeToUserMessage(body: ApiErr | null): string | null {
  switch (body?.code) {
    case "auth.account_locked":
    case "auth.locked": {
      const retrySeconds = body.retry_after_seconds;
      if (typeof retrySeconds === "number" && Number.isFinite(retrySeconds) && retrySeconds > 0) {
        const retryMinutes = Math.max(1, Math.ceil(retrySeconds / 60));
        return `Account temporarily locked. Try again in ${retryMinutes} minutes.`;
      }
      return "Account temporarily locked. Try again later.";
    }
    case "auth.email_unverified":
    case "auth.verification_pending":
      return "Verify your email before signing in. Check your inbox for the verification link.";
    case "auth.invalid_credentials":
      return "Invalid email or password.";
    default:
      return null;
  }
}

function normalizeRequestId(value: string | null | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed || undefined;
}

function requestIdFromBody(body: ApiErr | null): string | undefined {
  return normalizeRequestId(body?.request_id);
}

function messageWithRequestId(message: string, requestId: string | undefined): string {
  return requestId ? `${message} Request ID: ${requestId}` : message;
}

export class ApiError extends Error {
  status: number;
  body: ApiErr | null;
  /** Backend correlation id from `request_id` / `X-Request-Id`, safe to show to users. */
  request_id: string | undefined;
  /** Camel-case alias for call sites that do not mirror the API envelope field name. */
  requestId: string | undefined;
  /** Safe, human-readable message for display in toasts and banners. */
  userMessage: string;
  constructor(
    status: number,
    body: ApiErr | null,
    message: string,
    requestId?: string | null,
  ) {
    super(message);
    this.status = status;
    this.body = body;
    this.request_id = requestIdFromBody(body) ?? normalizeRequestId(requestId);
    this.requestId = this.request_id;
    this.userMessage = messageWithRequestId(
      authCodeToUserMessage(body) ?? categoryToUserMessage(body?.status?.category),
      this.request_id,
    );
  }

  get code(): string | undefined {
    return typeof this.body?.code === "string" ? this.body.code : undefined;
  }
}

async function rawRequest(path: string, init: RequestInit = {}): Promise<unknown> {
  const headers = new Headers(init.headers || {});
  if (init.body && !(init.body instanceof FormData) && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  const res = await fetch(`${BASE}${path}`, { ...init, headers, credentials: "include" });
  let body: unknown = null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    body = await res.json();
  }
  if (!res.ok) {
    // The body, when present, is the `{data:null, status:{...}, errors?}`
    // envelope; narrow on the shape rather than reaching through `any`.
    const errBody = (body && typeof body === "object" ? (body as ApiErr) : null);
    const msg = errBody?.status?.message || res.statusText;
    throw new ApiError(res.status, errBody, msg, res.headers.get("x-request-id"));
  }
  // Successful envelope — body is `{data, status}`. Pull `.data` after a
  // structural check so a non-conforming response surfaces as `null`
  // rather than crashing the caller.
  if (body && typeof body === "object" && "data" in body) {
    return (body as { data: unknown }).data ?? null;
  }
  return null;
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

/**
 * Fetch a paged endpoint that returns `{items: T[], next_cursor: string | null}`.
 *
 * Unwraps the outer `{data, status}` envelope, then returns the typed
 * inner payload.  Throws `ApiError` on non-2xx exactly like `api.get`.
 *
 * Usage:
 *   const page = await getPaged<Part>("/parts?limit=50");
 *   // page.items: Part[]
 *   // page.next_cursor: string | null
 */
export type PagedResponse<T> = { items: T[]; next_cursor: string | null };

export async function getPaged<T>(
  path: string,
  opts?: ApiOptions,
): Promise<PagedResponse<T>> {
  const data = await rawRequest(path, { signal: opts?.signal });
  // The outer envelope is already unwrapped by rawRequest; `data` is the
  // inner payload `{items, next_cursor}`.
  if (
    data &&
    typeof data === "object" &&
    "items" in data &&
    Array.isArray((data as { items: unknown }).items)
  ) {
    return data as PagedResponse<T>;
  }
  // Fallback: treat as empty page (should not happen with a conforming server).
  return { items: [], next_cursor: null };
}

export const api = {
  // --- legacy untyped flavour ---
  // The body is typed via an explicit generic `B = unknown`. Untyped
  // call-sites still compile (TS infers `B = unknown`); typed call-sites
  // can pass `<{id: string}, AddStockRequest>` so a backend Pydantic
  // change that drops a field surfaces as a TS error in the form
  // builder rather than silent FE/BE drift.
  get: <T>(p: string, opts?: ApiOptions) => request<T>(p, { signal: opts?.signal }),
  post: <T, B = unknown>(p: string, body?: B, opts?: ApiOptions) =>
    request<T>(p, {
      method: "POST",
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: opts?.signal,
    }),
  patch: <T, B = unknown>(p: string, body?: B, opts?: ApiOptions) =>
    request<T>(p, {
      method: "PATCH",
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: opts?.signal,
    }),
  // PUT is for endpoints that REPLACE a resource wholesale rather than
  // merging a partial (PATCH). Currently only `/parts/{id}/eda`, whose
  // server contract is explicitly a full replacement — an omitted field
  // resets to its default.
  put: <T, B = unknown>(p: string, body?: B, opts?: ApiOptions) =>
    request<T>(p, {
      method: "PUT",
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
    post: <S extends ZodType, B = unknown>(p: string, schema: S, body?: B, opts?: ApiOptions) =>
      parsedRequest(p, schema, {
        method: "POST",
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: opts?.signal,
      }),
    patch: <S extends ZodType, B = unknown>(p: string, schema: S, body?: B, opts?: ApiOptions) =>
      parsedRequest(p, schema, {
        method: "PATCH",
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: opts?.signal,
      }),
    delete: <S extends ZodType>(p: string, schema: S, opts?: ApiOptions) =>
      parsedRequest(p, schema, { method: "DELETE", signal: opts?.signal }),
  },
};

/**
 * Shape spread by `core/responses.py::http_exception_handler` onto the
 * 409 response body when create-part collides on the partial unique
 * MPN index (CLAUDE.md hard invariant). The dict is assembled in
 * `parts.py::create_part`'s `HTTPException(detail={…})` block.
 */
export type MpnConflictDetail = {
  message: string;
  existing_id: string;
  existing_name: string;
};

/**
 * Narrow an unknown caught error to the MPN conflict body if and only
 * if it's an `ApiError(409)` whose body carries `existing_id` and
 * `existing_name` strings. Returns `null` for any other shape so call
 * sites don't have to reach through `any`.
 *
 * Use at the catch site:
 *   const detail = getConflictDetail(err);
 *   if (detail) showConflictDialog(detail.existing_name, detail.existing_id);
 */
export function getConflictDetail(err: unknown): MpnConflictDetail | null {
  if (!(err instanceof ApiError) || err.status !== 409 || !err.body) return null;
  // The HTTPException(detail={…}) dict is spread onto the envelope by
  // the server-side handler, so the top-level body has the fields
  // directly (NOT under a nested `detail` key).
  const b = err.body as Record<string, unknown>;
  if (typeof b.existing_id !== "string" || typeof b.existing_name !== "string") return null;
  const message = typeof b.message === "string" ? b.message : "";
  return {
    message,
    existing_id: b.existing_id,
    existing_name: b.existing_name,
  };
}
