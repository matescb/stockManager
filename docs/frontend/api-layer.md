# API layer

Audience: engineer

`web/src/lib/api.ts` is the single seam every HTTP call goes through. It
unwraps the backend's `{data, status}` envelope, throws a uniform
`ApiError`, and threads the session cookie via `credentials: "include"`.
The envelope itself is documented in
[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) and CLAUDE.md (hard
invariants); this page covers only the client wrapper.

## `api.*` — the legacy untyped flavour

Five verbs, all returning `body.data` cast to `T`:

```ts
// web/src/lib/api.ts:163-186
api.get<T>(path, opts?)
api.post<T, B = unknown>(path, body?, opts?)
api.patch<T, B = unknown>(path, body?, opts?)
api.delete<T>(path, opts?)
api.upload<T>(path, FormData, opts?)
```

`opts.signal` is an `AbortSignal` so callers outside `useQuery` (raw
`useEffect`, the bootstrap effect in `AuthProvider`) can cancel an in-flight
request on unmount (`api.ts:128-129`, v1 FE HIGH-4). `useQuery` already
provides one via its `queryFn` argument.

The cast is unsound — the server can change shape and the FE silently keeps
running. New code and security-sensitive paths should use `parsed.*` instead
(`api.ts:6-14`).

## `api.parsed.*` — the Zod-validating flavour

Same surface, but takes a Zod schema and parses `body.data` against it
(`api.ts:188-206`):

```ts
// web/src/lib/api.ts:189-206
api.parsed.get(path, schema, opts?)
api.parsed.post(path, schema, body?, opts?)
api.parsed.patch(path, schema, body?, opts?)
api.parsed.delete(path, schema, opts?)
```

A schema mismatch throws `ApiError(0, body, msg)` where `body.errors` is the
flattened Zod issue list, mirroring the backend's 422 shape so callers can
inspect `body.errors` uniformly (`api.ts:103-118`):

```ts
// web/src/lib/api.ts:104-117
const errors = result.error.issues.map((i) => ({
  field: i.path.join(".") || "<root>",
  message: i.message,
}));
throw new ApiError(0, { data: null, status: { category: "client_schema_mismatch", … }, errors }, …);
```

Migration is opt-in. The highest-stakes adopter today is `/auth/me` —
schema drift there cascades into every authed page, so `AuthProvider` uses
`api.parsed.get("/auth/me", MeSchema)` (`auth.tsx:55`, `auth.tsx:118`).

Schemas live in `web/src/lib/schemas.ts`. Default behaviour:

- Unknown fields are stripped (no `.strict()`), so backend additions are
  forward-compatible (`schemas.ts:19-25`).
- Missing required fields throw at parse time → `ApiError(0)`.

## `getPaged<T>` — cursor pagination

Separate helper for `{items, next_cursor}` endpoints (`api.ts:144-161`).
Unwraps the outer envelope, then returns the typed inner payload:

```ts
// web/src/lib/api.ts:142-161
export type PagedResponse<T> = { items: T[]; next_cursor: string | null };
export async function getPaged<T>(path, opts?): Promise<PagedResponse<T>> { … }
```

A non-conforming server response falls back to `{ items: [], next_cursor: null }`
rather than throwing (`api.ts:159-160`).

## `ApiError`

```ts
// web/src/lib/api.ts:48-59
export class ApiError extends Error {
  status: number;        // HTTP status, or 0 for schema mismatch
  body: ApiErr | null;   // parsed envelope (with errors[] when 422)
  userMessage: string;   // safe-for-display string from categoryToUserMessage
}
```

Three flavours of `status`:

| `status` | When | `body.status.category` |
|---|---|---|
| `>= 400` | HTTP error response | from server (`unauthenticated`, `conflict`, …) |
| `0` | Zod schema mismatch in `api.parsed.*` | `client_schema_mismatch` |
| The original status | Fetch error before headers (rare) | server message or `res.statusText` |

The fetch core (`rawRequest`, `api.ts:61-86`) only parses JSON when
`content-type` includes `application/json`; non-JSON 4xx/5xx still surface
as `ApiError` with `body: null` and `msg = res.statusText`.

### Pinning tests

`web/src/lib/__dom__/api.401.dom.test.ts` covers the 401 happy path,
envelope unwrap, and the `credentials: "include"` invariant (every request).
The 401 → redirect chain itself lives in `main.tsx`; see
[auth-flow](auth-flow.md).

## `categoryToUserMessage`

`api.ts:31-46`. The backend's `status.category` is a stable enum the FE
maps to a human string for toasts and banners:

```ts
// web/src/lib/api.ts:31-46
switch (category) {
  case "unauthenticated":   return "Session expired. Please sign in again.";
  case "forbidden":         return "You don't have permission to do that.";
  case "not_found":         return "Not found.";
  case "conflict":          return "That's a duplicate or conflicts with existing data.";
  case "validation_error":  return "Some fields don't look right. Check the form and retry.";
  default:                  return "Something went wrong. Try again, or refresh.";
}
```

The raw `status.message` from the server stays on `ApiError.message` for
console / Sentry; `userMessage` is what UI code shows. `MpnLookup` is a
typical caller (`web/src/components/MpnLookup.tsx:45`):

```tsx
// web/src/components/MpnLookup.tsx:44-46
} catch (e) {
  setNote(e instanceof ApiError ? e.userMessage : "Lookup failed");
}
```

## `getConflictDetail` — narrowing a 409

The MPN-uniqueness 409 (CLAUDE.md hard invariant — partial unique index
`uq_parts_ws_mpn`) returns `{existing_id, existing_name, message}` spread
onto the envelope by `core/responses.py::http_exception_handler`.
`getConflictDetail` narrows an unknown error to that shape so call sites
don't reach through `any` (`api.ts:209-244`):

```ts
// web/src/lib/api.ts:231-244
export function getConflictDetail(err: unknown): MpnConflictDetail | null {
  if (!(err instanceof ApiError) || err.status !== 409 || !err.body) return null;
  const b = err.body as Record<string, unknown>;
  if (typeof b.existing_id !== "string" || typeof b.existing_name !== "string") return null;
  return { message: …, existing_id: b.existing_id, existing_name: b.existing_name };
}
```

Returns `null` for any other shape — call sites pattern-match:

```ts
const detail = getConflictDetail(err);
if (detail) showConflictDialog(detail.existing_name, detail.existing_id);
```

The fields are at the top level of `body`, NOT under a nested `detail` key —
`HTTPException(detail={…})` is spread onto the envelope server-side, see
the comment at `api.ts:233-236`.

## Per-resource Zod schemas

`web/src/lib/schemas.ts` mirrors `backend/app/schemas/*` Pydantic models.
Each schema exports both the Zod object and the inferred TS type, and
`web/src/types.ts` re-exports the inferred types so consumers have a
single source of truth.

Atom helpers (`schemas.ts:32-35`):
- `uuid` = `z.string().uuid()`
- `isoDate` = `z.string()` (kept as ISO string, NOT parsed to `Date`)
- `nullableString` / `nullableNumber`

Resource schemas: `PartSchema`, `StorageLocationSchema`, `LotSchema`,
`StockEntrySchema`, `ProjectSchema`, `OrderSchema`, `OrderEntrySchema`,
`BuildSchema`, `ProjectEntrySchema`, `CustomFieldRowSchema`, `MeSchema`.
List variants (`PartsListSchema`, `OrdersListSchema`, …) wrap them in
`z.array()`. `PagedPartsSchema` is the only paged-shape schema currently
defined (`schemas.ts:74-78`).

When a backend Pydantic schema changes, update the matching Zod schema
here. Drift surfaces as a parse error in the running app — not a silent UI
break days later (`schemas.ts:14-17`).
