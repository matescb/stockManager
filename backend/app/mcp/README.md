# mcp

Audience: engineer

The Model Context Protocol server mounted at `/mcp` — the surface an AI
assistant connects to. A protocol adapter, not a domain: every tool calls the
same `app/domain/*/service.py` functions the REST routes call.

Read [docs/api/mcp.md](../../../docs/api/mcp.md) for the surface itself and
[ADR-0030](../../../docs/adr/0030-mcp-server-surface.md) for why it is shaped
this way. Don't restate either here.

## Files

| File | What |
|---|---|
| `server.py` | The `MCPServer` instance, the stateless streamable-HTTP transport, the ASGI dispatcher, `lifespan_context` and `mount_mcp` — the two things `main.py` calls |
| `auth.py` | `McpAuthMiddleware` — authenticates every request, binds the principal, or answers one 401 |
| `principal.py` | `Principal` (the contextvar), `Caller`, and `unit_of_work` — the per-tool-call session and transaction |
| `tools/_registry.py` | The `@tool` decorator: session, write gate, rate limit, error translation |
| `tools/_shared.py` | Resolution (`resolve_part`, `resolve_category`, …), result serialisation, and the `audit` helper |
| `tools/read.py` | The 10 read tools |
| `tools/sourcing.py` | `sourcing_offers` — a read-shaped tool declared as a write |
| `tools/write.py` | The 4 KiCad-library write tools |
| `tools/write_inventory.py` | The 4 inventory write tools (stock, categories) |

## Public surface

| Operation | Entry point |
|---|---|
| Mount the server | `server.mount_mcp(app)` |
| Start the session manager | `server.lifespan_context(app)` (from `main.py`'s own lifespan) |
| Add a tool | `@tool()` / `@tool(writes=True)` in a `tools/` module, listed in `tools/__init__.py::load_tools` |
| Who is calling | `principal.current()`, or the `Caller` a tool is handed |

## Hard rules

- **A tool declares `writes=True` if it costs anything** — money, a metered
  quota, an outbound request, or a row. Not "if it looks like a mutation":
  `sourcing_offers` returns a price list and is a write, because a cache miss
  spends the tenant's distributor quota. The flag is the entire write gate
  (read-only token first, then viewer role), so getting it wrong hands a
  read-only credential a cost it should not be able to incur.
- **Declaring it wrong fails at runtime, not just in review.**
  `unit_of_work(writes=False)` watches the connection for DML and refuses the
  commit, so a misdeclared tool returns `mcp.undeclared_write` with its changes
  discarded. The structural write-set test is change detection only — both its
  sides come from the same declaration.
- **Every tool declares a rate ceiling.** `/mcp` gets no slowapi decorators, so
  a tool without one is an uncapped endpoint. Match the REST twin's number
  where there is one.
- **Tool docstrings are the agent-facing contract.** They are what the model
  reads to decide whether and how to call. Write them for someone who has never
  seen this codebase; put implementation notes in comments instead.
- **Every query filters by `caller.ws.id`.** There is no route to hang
  `assert_in_workspace` on, so the helpers in `_shared.py` do it and tools use
  them rather than querying directly.
- **Every mutation writes the audit row its REST twin writes** — same action
  name, same comment grammar, attributed to the token owner. Stock movements
  write none, because the routes write none.
- **Stateless transport is a correctness constraint, not a preference.** The
  principal contextvar only reaches the tool because the request is handled
  inline. See ADR-0030.
- **The session manager is rebuilt per lifespan entry.** The SDK's is
  single-use; a manager built at import would make the second test in every
  file fail.

## See also

- [docs/api/mcp.md](../../../docs/api/mcp.md) — the surface
- [ADR-0030](../../../docs/adr/0030-mcp-server-surface.md) — the decisions
- [ADR-0029](../../../docs/adr/0029-api-tokens-and-csrf-exemption.md) — the credential
- `app/core/deps.py::resolve_live_token` — the shared half of token authentication
- `backend/tests/test_mcp.py` — the whole contract, executable

## Don't

- Don't add a tool that queries a `WorkspaceOwned` table without a workspace
  filter, or that writes without going through the domain service.
- Don't switch the transport to stateful without moving the principal off the
  contextvar first — it would silently cross tenants.
- Don't re-implement the token resolution, membership re-check or telemetry;
  call `deps.resolve_live_token`.
- Don't return a raw exception or a stack trace from a tool. Anticipated
  failures are `ToolError` carrying the app's error code; anything else is a
  crash and the SDK will hide it for you.
