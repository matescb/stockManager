# Agent API

Audience: engineer

How a script, an LLM agent, or any non-browser client drives the whole REST
surface with a personal access token. Assumes you have read nothing else in
this repo.

The one thing to know up front: there is no separate agent API. A token-authed
request hits the same routes, the same envelope and the same permission checks
as the web app, and is refused on exactly one class of route (see
[Session-only routes](#session-only-routes)). Everything below is that
statement made precise.

## Authenticate

1. Mint a token in the web app: **Settings → API tokens** (`/settings/api-tokens`)
   → **Create API token**. Choose `read_only` unless the agent writes. The
   plaintext is shown once and is not recoverable — the server stores only an
   HMAC.
2. Send it on every request:

```
Authorization: Token smk_3f1c…b9.KJ3n…Qw
```

`Bearer` works identically and is what most agent tooling emits; both schemes
are matched case-insensitively (`backend/app/core/deps.py:69`).

Five rules follow, all enforced in `deps.py::_authenticate_api_token`
(`backend/app/core/deps.py:56-147`). Full reasoning in
[tokens](tokens.md#using-a-token) — the short version:

| Rule | Consequence for an agent |
|---|---|
| Any non-empty `Authorization` header commits the request to the token path | A bad header is `401` even from a browser with a live session. There is no cookie fallback. |
| The workspace is **pinned** to the one the token was minted in | You never send `X-Workspace-Id`. Sending a *different* one is `403 auth.token_workspace_mismatch`; sending the same one is accepted. |
| The owner's membership role still applies | A viewer's token gets `403 resource.insufficient_role` on writes even when `read_only` is `false`. |
| Membership is re-checked per request | Losing the seat is `401 auth.invalid_token` on every route, and removing a member revokes their tokens outright. |
| `read_only` refuses every method outside `GET` / `HEAD` / `OPTIONS` | `403 auth.token_read_only`, raised before any handler runs — so a refused write cannot be used to probe whether a resource exists. |

### No CSRF, no Origin

Do **not** send an `Origin` header, and do not look for a CSRF token. The
browser-facing `CsrfOriginMiddleware` skips its Origin check whenever an
`Authorization` header is present, which is sound precisely because the header
excludes cookie auth ([ADR-0029](../adr/0029-api-tokens-and-csrf-exemption.md)).
Every write in `backend/tests/test_agent_rest_smoke.py` runs with no `Origin`
and no cookie for exactly this reason.

### Expiry and revocation

`expires_in_days` is 1–365, or `null` for no expiry — the normal choice for an
unattended agent. Revocation is immediate and one-way; an expired or revoked
token is indistinguishable from a malformed one (`401 auth.invalid_token`, one
code for every failure mode, deliberately). Both mint and revoke need a browser
session; see below.

### Session-only routes

These refuse a token-authed request with `403 auth.token_no_token_management`,
whatever the method (`core/deps.py::forbid_api_token`,
`backend/app/core/deps.py:381-403`):

| Method | Path |
|---|---|
| `GET`, `POST` | `/api/tokens` |
| `POST` | `/api/tokens/{token_id}/revoke` |
| `POST` | `/api/workspaces` |
| `PATCH` | `/api/workspaces/current` |
| `POST` | `/api/workspaces/current/catalog/tokens` |
| `DELETE` | `/api/workspaces/current/catalog/tokens/{token_id}` |
| `PATCH`, `DELETE` | `/api/workspaces/members/{member_id}` |
| `POST` | `/api/workspaces/{workspace_id}/switch` |
| `POST`, `DELETE` | `/api/invitations`, `/api/invitations/{invitation_id}` |
| `POST` | `/api/invitations/accept` |

The rule behind the list: a leaked token must not be able to mint a successor,
mint a second credential that outlives its own revocation, invite an accomplice,
change roles, or erase the `last_used_at` trail that would expose the intrusion.
An agent that hits one of these should stop and ask a human to do it in a
browser — retrying will not help, and neither will a different token.

The list is generated from the app's dependency graph and pinned by
`test_agent_rest_smoke.py::test_the_blocked_surface_list_matches_the_code`, so
it cannot drift from the code. Reads of the same areas are *not* blocked:
`GET /api/workspaces/members`, `GET /api/invitations` and
`GET /api/workspaces/current/catalog/tokens` all answer a token normally.

### Rate limits

slowapi, enabled only when `APP_ENV=prod` (`backend/app/core/ratelimit.py:23`),
bucketed per workspace on most routes and per user on token minting. Live
examples: search and category writes `30/minute`, EDA uploads `20/minute`, EDA
config writes `60/minute`. A limited response is `429` in the normal envelope
plus `code: "rate_limited"` and `retry_after_seconds`
(`backend/app/main.py:306-336`); `Retry-After` and `X-RateLimit-*` headers are
set too. Back off on `retry_after_seconds` rather than guessing.

## The envelope contract

Every `/api` response — success and error alike — is `{ data, status }`. See
[API conventions](./README.md#envelope). Three shapes an agent must handle:

**Error.** `data` is `null`, `status.category` is derived from the HTTP status,
and `code` sits at the **top level**, not inside `status`:

```json
{ "data": null, "status": { "category": "forbidden", "message": "read-only api token" },
  "code": "auth.token_read_only", "request_id": "…" }
```

Switch on `code`, never on `status.message` — the message is human-facing and
may be reworded. Routes may spread extra top-level keys alongside it (a part
`409` carries `existing_id` and `existing_name`). The full constant list is
`ErrorCodes` in `backend/app/core/errors.py:87-310`; the ones an agent will
actually branch on:

| Code | Status | Meaning |
|---|---|---|
| `auth.not_authenticated` | 401 | No credential at all. |
| `auth.invalid_token` | 401 | Malformed, unknown, wrong secret, revoked, expired, or the owner lost their seat. **One code for all of them** — do not try to tell them apart, there is deliberately nothing to recover. |
| `auth.token_read_only` | 403 | Write attempted with a `read_only` token. Mint a writing token; retrying will not help. |
| `auth.token_workspace_mismatch` | 403 | You sent `X-Workspace-Id`. Drop the header. |
| `auth.token_no_token_management` | 403 | A session-only route. Needs a human in a browser. |
| `resource.insufficient_role` | 403 | The owner's membership role is too low. Carries `required_role`. |
| `rate_limited` | 429 | Carries `retry_after_seconds`. |

**Cross-workspace is `404`, never `403`.** An id belonging to another workspace
is indistinguishable from one that does not exist. This is the workspace
isolation invariant ([ADR-0002](../adr/0002-code-enforced-workspace-isolation.md)),
not an oversight — do not treat a `404` as "wrong permissions, retry as
someone else".

**`422` validation** adds an `errors` array of `{field, message}`, where `field`
is the dotted path into the request body
(`backend/app/core/responses.py:144-166`). Input schemas are
`extra="forbid"`, so an unrecognised key is a `422` rather than a silent
ignore — useful, since a typo'd field name fails loudly instead of writing a
default.

## Working vocabulary

One row per area. Prefixes are the mount points in `backend/app/main.py:548-649`.

| Area | Prefix | What an agent does with it | Reference |
|---|---|---|---|
| Parts | `/api/parts` | The central entity. Create, search by `mpn`, read `on_hand` / `available`, patch metadata, attach files. | [parts](parts.md) |
| Categories | `/api/categories` | File parts into a tree that also supplies KiCad defaults (refdes prefix, symbol/footprint refs). | [categories](categories.md) |
| EDA | `/api/eda`, `/api/parts/{id}/eda` | Upload `.kicad_sym` / `.kicad_mod` / STEP / SPICE, wire them to a part, import a vendor zip. Multipart. | [eda](eda.md) |
| Stock | `/api/stock` | `add` / `remove` / `move` / `adjust`. Append-only ledger — never patch a quantity, post a delta. | [stock](stock.md) |
| Lots | `/api/lots` | Batch/date-code identity within a part. | [stock](stock.md) |
| Storage | `/api/storage` | Locations, plus per-location on-hand and history. | [storage](storage.md) |
| Projects & BOM | `/api/projects`, `/api/bom-presets` | BOM lines, substitutes, import presets. | [projects](projects.md) |
| Builds | `/api/builds` | Reserve, consume, shortage analysis against a project BOM. | [builds](builds.md) |
| Orders | `/api/orders` | Purchase/sales orders and the receive workflow. | [orders](orders.md) |
| Sourcing | `/api/sourcing`, `/api/workspaces/current/sourcing` | Distributor offers, purchase plans, alerts. Outbound calls, so budgeted and cached. | [sourcing](sourcing.md) |
| Reports | `/api/reports` | Aggregates: low-stock, stock value, BOM shortage, expiring lots. The read to poll. | [reports](reports.md) |
| Search | `/api/search?q=` | One query across parts, storage, projects, lots, orders. | [search](search.md) |
| Attachments, tags, custom fields | `/api/attachments`, `/api/tags`, `/api/custom-fields` | Polymorphic surfaces over most entities. | [attachments-tags-cf](attachments-tags-cf.md) |
| Audit | `/api/audit` | Read the trail. Admin+ only, so a member's token gets `403 resource.insufficient_role` here. | [audit](audit.md) |
| Tokens | `/api/tokens` | **Session-only.** Listed for completeness — a token cannot reach it. | [tokens](tokens.md) |
| KiCad | `/kicad-api` | Not for agents — KiCad's own read-only protocols, outside the envelope. Same token. | [kicad](kicad.md) |
| MCP | `/mcp` | The other door for an assistant: named tools over these same services, one JSON-RPC endpoint. | [mcp](mcp.md) |

Writes are attributed to the token's **owner**, not to the token: audit rows and
ledger entries carry that user id, exactly as if they had clicked the button.

## Quickstart

The walk below is the one `backend/tests/test_agent_rest_smoke.py` runs on every
CI build, with the same payloads — if an example here stops working, that module
goes red first. Set up:

```bash
export SM=https://parts.example.com
export TOKEN='smk_3f1c…b9.KJ3n…Qw'
export AUTH="Authorization: Token $TOKEN"
```

**1. Confirm the credential and learn your workspace.**

```bash
curl -sS -H "$AUTH" "$SM/api/auth/me"
```

Returns the pinned workspace and nothing else — a token never enumerates its
owner's other tenants.

**2. Create a category.**

```bash
curl -sS -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name": "Resistors", "refdes_prefix": "R"}' \
  "$SM/api/categories"
```

`201`, and `data.id` is the category id used below.

**3. Create a part in it.**

```bash
curl -sS -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name": "R-10k-0402", "part_type": "local", "mpn": "RC0402FR-0710KL",
       "category_id": "<CATEGORY_ID>", "low_stock_report_quantity": 100}' \
  "$SM/api/parts"
```

A duplicate `mpn` in the workspace is `409` carrying `existing_id` and
`existing_name` — treat that as "found it" rather than an error, and reuse the
id ([ADR-0004](../adr/0004-mpn-uniqueness-per-workspace.md)).

**4. Upload a symbol and a footprint** — multipart, same header:

```bash
curl -sS -H "$AUTH" -F 'file=@R_10k.kicad_sym' "$SM/api/eda/symbols"
curl -sS -H "$AUTH" -F 'file=@R_0402.kicad_mod' "$SM/api/eda/footprints"
```

The store is content-addressed: re-uploading identical bytes under the same name
answers `200` with the existing row instead of `201`, so retries are safe.

**5. Wire them to the part.** `PUT` is a full replacement, not a merge — every
omitted field is written as its default, which is the only way "clear the
symbol" is expressible:

```bash
curl -sS -X PUT -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"symbol_id": "<SYMBOL_ID>", "footprint_id": "<FOOTPRINT_ID>",
       "value": "10k", "footprint_filters": ["R_0402*"]}' \
  "$SM/api/parts/<PART_ID>/eda"
```

**6. Stock it in.** Quantity is a ledger delta, never a field you set:

```bash
curl -sS -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"part_id": "<PART_ID>", "quantity": 25, "storage_location_id": "<BIN_ID>"}' \
  "$SM/api/stock/add"
```

**7. Read availability back.**

```bash
curl -sS -H "$AUTH" "$SM/api/parts/<PART_ID>"
```

`data.on_hand` is 25 and `data.available` is `on_hand` minus reserved. There is
no quantity column anywhere — every number comes from summing the ledger
([ADR-0001](../adr/0001-append-only-stock-ledger.md)), which is why an agent
must post deltas rather than trying to set a total.

**8. Poll what needs attention.**

```bash
curl -sS -H "$AUTH" "$SM/api/reports/low-stock"
curl -sS -H "$AUTH" --get --data-urlencode 'q=R-10k-0402' "$SM/api/search"
```

Low-stock rows carry `on_hand`, `reserved`, `available`, `threshold` and
`short_by`, sorted by `short_by` descending — the report to drive a reorder
agent from.

## Machine-readable schema

`GET /openapi.json` in non-prod environments (it is disabled in prod on
purpose — `backend/app/main.py:292-294`). The schema declares both credentials
under `components.securitySchemes`: `ApiToken` (HTTP bearer) and
`SessionCookie`. The declaration is documentation only — it adds no dependency
to any route, so it cannot be read as a statement about which routes are
actually protected; `deps.py` remains the only thing that authenticates a
request. `/docs` renders it with an Authorize button.

## KiCad

If the goal is KiCad's HTTP library rather than a general agent, the token is
the same credential but the surface is not: `/kicad-api/v1/*` is `GET`-only,
raw JSON without the envelope, and answers `404` to everything it does not
like. Use a `read_only` token — its plaintext lands in a `.kicad_httplib` file
on a workstation. See
[tokens § Using a token with KiCad](tokens.md#using-a-token-with-kicad) for the
file format, [kicad](kicad.md) for both protocols, and `GET /api/eda/kicad-setup`
for the values to write into it.

## MCP

If the client is an AI assistant rather than a script, there is a second door:
an MCP server at `/mcp`, authenticated by the same token, exposing 19 named
tools over the same services these routes call. It exists because a general
REST surface makes an assistant spend its context re-deriving the domain model.
Prefer it for assistants; prefer the routes above for anything scripted. See
[mcp](mcp.md).

## See also

- [mcp](mcp.md) — the MCP server, for AI assistants
- [eda](eda.md) — the CAD library surface, and the setup endpoint
- [kicad](kicad.md) — the two KiCad protocols this token also opens
- [tokens](tokens.md) — the credential itself: minting, model, error codes
- [API conventions](./README.md) — envelope, error body, pagination
- [ADR-0029](../adr/0029-api-tokens-and-csrf-exemption.md) — token design and the CSRF exemption
- [ADR-0002](../adr/0002-code-enforced-workspace-isolation.md) — why cross-workspace is `404`
- `backend/tests/test_agent_rest_smoke.py` — the whole of this page, executable
