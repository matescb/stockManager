# ADR-0003: API envelope `{ data, status }`

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

Two common shapes for a JSON API: bare payload (`GET /parts/123` returns the part object directly) or wrapped envelope (returns `{ data: <part>, status: {...} }`). Bare payloads are terser but force every error path to invent its own shape, and they leave no room for cross-cutting metadata (request id, deprecation warning, paging) without changing the success type.

The wrapped envelope makes the success and error shapes structurally similar — both have `status.category` and `status.message` — so the client wrapper can do one envelope-unwrap step and one error-throw step regardless of the route.

## Decision

Every backend response is `{ data, status }`. Server-side, routes call `responses.ok(payload)` or `responses.err(category, message, ...)` from `backend/app/core/responses.py`. Errors raised as `HTTPException(detail={...})` are caught by `core/responses.py::http_exception_handler` and the `detail` dict is spread onto the envelope (so e.g. a 409 returns `{ data: null, status: {...}, existing_id, existing_name }`).

Client-side, `web/src/lib/api.ts` unwraps `data` from successful responses and throws `ApiError(status, body, msg)` on non-2xx. Callers see the unwrapped payload type or a thrown `ApiError`; no caller touches the envelope directly.

## Consequences

- **Good**: One client wrapper handles every route. Errors carry structured fields (the `HTTPException(detail=…)` dict) without each route inventing a shape. `ApiError.body` is the same dict the server raised, which makes 409-handling helpers like `extractMpnConflict` (`web/src/lib/api.ts:230`) trivial.
- **Trade-offs**: Every successful response carries an extra `status: {category, message}` object, ~30 bytes overhead. Hand-crafted curl reads are slightly noisier — `jq .data` is required.
- **What it forbids**:
  - Don't return a bare payload from a route. Always go through `responses.ok()` (or let the framework default raise an `HTTPException`).
  - Don't bypass `lib/api.ts` on the client by calling `fetch` directly — the session cookie won't ride along (`credentials: "include"`) and the envelope won't be unwrapped.
  - Don't put error context in the `message` string; put it in the `HTTPException(detail={...})` dict so the client gets it as structured fields.
  - Don't rename `data` / `status` — both ends are coupled on those keys.

## Alternatives considered

- **Bare payloads (REST-by-the-book)** — rejected because every error path has to invent a shape, and cross-cutting metadata (request id) has no home.
- **JSON:API** — rejected as too prescriptive for a single-frontend app. `{ data, status }` gives the same "wrap everything" benefit without `included[]`, `links{}`, `meta{}`, `relationships{}` boilerplate that the frontend would never use.

## References

- Source: `backend/app/core/responses.py:58-72` (`ok`)
- Source: `backend/app/core/responses.py:72-94` (`err`)
- Source: `backend/app/core/responses.py:95-122` (`http_exception_handler`)
- Source: `web/src/lib/api.ts:21-22` (envelope types), `:48-89` (`ApiError`, unwrap)
- Rule: `CLAUDE.md:99-104`
- Architecture: `docs/ARCHITECTURE.md` — API envelope
