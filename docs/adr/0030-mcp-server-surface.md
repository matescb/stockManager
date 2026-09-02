# ADR-0030: The MCP server is an in-process, stateless mount over the service layer

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-09-02
- **Supersedes**: —
- **Superseded by**: —

## Context

Phase 7 established that an agent can drive the whole REST API with a personal
access token ([ADR-0029](0029-api-tokens-and-csrf-exemption.md)), and
[docs/api/agents.md](../api/agents.md) documents how. That works, and it stays.
But it puts the whole burden on the agent: read a page of prose, learn 15
routers, work out that `available` is `on_hand` minus `reserved`, discover that
a part's 3D model hangs off its footprint. In practice an assistant given a
bare REST surface spends its context re-deriving the domain model and still
gets the edges wrong.

The two things users actually asked for — *"maintaining 3D models"* and
*"getting info about parts when AI designing boards"* — are both narrow. They
want a handful of named operations with the domain's own vocabulary, not a
general-purpose HTTP client.

MCP is the protocol for exactly that, and both Claude Code and claude.ai speak
it over streamable HTTP. So the question was not whether, but where it lives
and what it is allowed to touch.

Three decisions had real alternatives.

**Where it runs.** A separate process (its own container, talking to the API
over HTTP) would isolate the dependency and let it scale on its own. It would
also need its own credential handling, its own copy of the workspace-isolation
rules, and a second place for every one of those rules to drift. The app runs
`--workers 1` on one small VPS ([ADR-0012](0012-uvicorn-single-worker-slowapi.md));
there is nothing to scale independently.

**What the tools call.** Tools could issue HTTP requests to our own API, which
would guarantee identical behaviour by construction. It would also mean a
request loop through uvicorn on every tool call, on a single worker, and an
authentication story that either forwards the caller's token or holds a second
credential.

**Session state.** The MCP streamable-HTTP transport supports stateful sessions
with resumable event streams. That is real functionality — and it is state to
expire, evict, and lose on restart, on a deployment that restarts on every
merge to `main`.

## Decision

**In-process, mounted at `/mcp`.** The official `mcp` Python SDK (pinned
`>=2.1.1`; note that 2.x renamed `FastMCP` to `MCPServer`), mounted into the
same FastAPI app, outside `/api` for the same reason `/kicad-api` and
`/catalog` are: it does not speak the `{data, status}` envelope. `MCP_ENABLED`
(default true) removes the mount entirely.

**Stateless transport**, and this is load-bearing rather than merely simpler.
In stateless mode the SDK handles each request inline in the caller's task. The
authenticated principal rides a `contextvar` set by the ASGI wrapper
(`app/mcp/principal.py`), and a contextvar only reaches the tool because the
tool runs inside the request's own context. Switch to stateful and request
handling moves into a task group started at lifespan time, which inherits *that*
task's context — and every tool would silently read the wrong tenant, or none.
`tests/test_mcp.py::test_concurrent_calls_do_not_cross_tenants` is the assertion
that fails if someone tries.

**Authenticated by an ASGI wrapper, not a dependency.** The MCP app is an opaque
sub-application with its own routing and its own streaming response lifecycle;
there is no endpoint signature to hang `Depends(...)` on. The wrapper builds a
`Request` from the raw scope and calls
`core/deps.py::resolve_live_token` — the shared half of the HTTP token path
(resolve, owner lookup, workspace-membership re-check, throttled `last_used_at`
telemetry). That function was extracted from `_authenticate_token_value` for
this: the membership re-check and the telemetry commit are precisely what a
second implementation would drift on.

What it deliberately does *not* reuse is the read-only gate, which asks
`request.method not in {GET, HEAD, OPTIONS}`. Every JSON-RPC call is a POST, so
applying it would refuse a read-only token even `tools/list`. The MCP surface
asks the same question **per tool** instead.

**Mounted as ASGI middleware, not a route.** Starlette's `Mount("/mcp", …)`
matches `/mcp/…` but not `/mcp`, so the bare path falls through to
`redirect_slashes` and answers `307` — which MCP clients do not follow on a
JSON-RPC POST, making the documented URL simply not work. A pure-ASGI dispatcher
in the middleware stack answers both forms. It sits inside `RequestIdMiddleware`
(so audit rows carry a request id) and outside CORS and the CSRF guard.

**Tools call the domain service layer.** `domain/stock/service.py`,
`domain/eda/service.py`, `domain/eda/importer.py`, `domain/categories/service.py`
and the rest — the same functions the routes call, with the same
`workspace_id` filters, the same `PartEdaIn` validation, the same
`ImportPlan` pipeline. A tool that needed to re-implement one of those would be
the bug.

Consequences of owning the transaction rather than borrowing `get_db`'s: each
tool call opens one session, commits on success and rolls back on any failure,
mirroring `cli/run_job.py`. Domain services only `flush()`, so without that
commit every MCP mutation would be silently discarded.

**Same contracts as the routes, restated where they cannot be inherited:**

- *Isolation.* Every query filters on the token's workspace; a cross-workspace
  id is `*.not_found`, never a permission error
  ([ADR-0002](0002-code-enforced-workspace-isolation.md)).
- *Role.* Writes require `member`+, the floor `require_member_for_writes`
  applies — re-checked here because there is no router to attach it to.
- *Audit.* Every mutation writes the row its REST twin writes, same action name
  and comment grammar ([ADR-0025](0025-universal-audit-log-policy.md)),
  attributed to the token's owner. Stock movements write none, because the
  routes write none: the ledger row is the record.

**Read-only tokens list every tool and are refused on call.** Hiding the write
tools would teach the assistant they do not exist, and it would then tell the
user the feature is missing rather than that the credential is wrong.

**`writes=True` follows the COST, not the shape of the answer.** A tool is a
write if it spends money, burns a metered quota, makes an outbound request, or
leaves a row behind — regardless of whether the thing it returns to the model
reads like an answer to a question. `sourcing_offers` is the case that settled
it: it looks like a lookup, and it shipped as `writes=False`, but a cache miss
reaches TrustedParts, spends part of the workspace's paid distributor quota and
writes a `sourcing_cache` row. A `read_only` token — the credential that ends up
in a config file on a workstation — could therefore spend the tenant's money.
The REST twin had it right all along: `POST /api/sourcing/search` is behind
`require_role("member")`. The cost is real and accepted: a read-only token can
no longer ask what a part costs.

**The declaration is checked at runtime, not only in a test.**
`unit_of_work(writes=False)` watches the session's connection for any
`INSERT`/`UPDATE`/`DELETE` and, if it sees one, rolls back and raises
`mcp.undeclared_write` instead of committing. Statement-level rather than
ORM-level deliberately: `Session.new`/`.dirty`/`.deleted` never see a Core
`pg_insert(...).on_conflict_do_update`, which is exactly how the sourcing cache
writes — an ORM-only guard would have missed the bug that motivated it. The
structural "is the declared write set what we expect" test remains, but only as
change detection for renames: both of its sides come from the same declaration,
so it cannot catch a declaration that is wrong, and it did not.

**Rate limits are enforced per tool, in the decorator.** slowapi's decorators
run inside FastAPI's endpoint wrapper, and this surface is an opaque ASGI mount
that short-circuits before the router — so every tool was uncapped, including
the three whose REST twins are the most expensive endpoints in the app. The
check cannot live in the ASGI wrapper either, which would have to parse a
JSON-RPC body to learn which tool is being asked for. `tools/_registry.py::tool`
is where the tool name and the verified workspace are both in scope, so the
ceiling is declared next to `writes=` and enforced there, against slowapi's own
limiter under a synthetic `mcp:{tool}:ws:{id}` key. Expensive tools match their
REST twins' numbers; everything else gets 120/minute.

**DNS-rebinding protection is off.** The SDK enables it by default for a
localhost-bound server, pinning `Host` to a list of local names. This server is
behind nginx on a deployment hostname, so the allow-list would have to track
that hostname or the surface `421`s — and the attack it prevents is already
impossible: a browser cannot attach the `Authorization` header this surface
requires to a cross-origin request without a CORS preflight, and
`CORS_ALLOW_HEADERS` deliberately omits `Authorization`. Same argument
ADR-0029 makes for the CSRF exemption.

## Consequences

- One place implements each rule. A new isolation or audit rule lands in the
  service layer and both surfaces get it.
- The MCP dependency is in the backend image. `mcp` pulls a transitive tree
  (`sse-starlette`, `httpx2`, `pyjwt`, `truststore`, `opentelemetry-api`); both
  locks carry it.
- A tool call runs on the single uvicorn worker. The SDK dispatches sync tools
  through `anyio.to_thread`, so blocking DB work does not stall the event loop,
  but a slow tool still occupies a worker thread. The ASGI wrapper's own
  authentication is offloaded the same way for the same reason — it is ~5
  synchronous queries plus a telemetry commit, and inline it would have blocked
  every `/api` request in flight, not just its own. The nginx block for `/mcp`
  uses a 5-minute read timeout and `proxy_buffering off` (the transport streams
  `text/event-stream`).
- Rate-limit buckets live in slowapi's per-process store, so they inherit
  ADR-0012's constraint: more than one uvicorn worker would multiply every
  ceiling, MCP's included.
- The session manager is single-use per instance, so it is rebuilt per lifespan
  entry. In production that is once per process; the test suite starts a
  lifespan per test.
- Switching to a stateful transport is not a configuration change. It would
  break tenant isolation and needs the principal moved out of the contextvar
  first.

## Alternatives considered

- **A separate MCP container.** Rejected: a second implementation of workspace
  isolation, role checks and audit, on a deployment with nothing to scale.
- **Tools calling our own REST API over HTTP.** Rejected: a request loop through
  a single-worker uvicorn per tool call, plus a second credential path.
- **Stateful sessions with resumable streams.** Rejected: state to expire and
  lose across the deploy-on-merge restart, and it breaks the contextvar the
  tenancy model rests on.
- **Hiding write tools from read-only tokens.** Rejected: an assistant that
  cannot see a tool reports the feature as missing.
- **A `Mount` plus documenting `/mcp/`.** Rejected: clients post to the URL the
  user pasted, and a `307` on a JSON-RPC POST is not followed.

## See also

- [docs/api/mcp.md](../api/mcp.md) — the surface, for someone connecting to it
- [ADR-0029](0029-api-tokens-and-csrf-exemption.md) — the credential
- [ADR-0025](0025-universal-audit-log-policy.md) — the audit rule being upheld
- [ADR-0012](0012-uvicorn-single-worker-slowapi.md) — why one worker
- `backend/app/mcp/README.md` — the module map
