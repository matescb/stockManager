# MCP Server

Audience: engineer

The Model Context Protocol server at `/mcp` — how an AI assistant connects to a
workspace, what tools it gets, and what it is not allowed to do.

This is the same inventory, reached a different way. Every tool calls the same
domain services the REST routes call, under the same workspace isolation, the
same role checks and the same audit trail. If you want a script or an agent to
drive the REST API directly instead, read [agents](agents.md) — that is the
lower-level door and it is not going away.

## Connect

1. Mint a token: **Settings → API tokens** → **Create API token**. Leave
   `read_only` unchecked if the assistant should be able to change anything;
   see [Read-only tokens](#read-only-tokens) for what a read-only one can still
   do.
2. Point the client at `https://<host>/mcp` with an `Authorization` header.

Claude Code:

```bash
claude mcp add --transport http stockmanager https://<host>/mcp \
  --header "Authorization: Token smk_3f1c…b9.KJ3n…Qw"
```

claude.ai (**Settings → Connectors → Add custom connector**): URL
`https://<host>/mcp`, header `Authorization: Token smk_…`.

Any MCP client that speaks streamable HTTP works; the server is stateless, so
there is no session to keep alive and no sticky routing to arrange.

### What the token pins

A token belongs to one **user** and one **workspace**, and nothing in the MCP
protocol can move it. The workspace is the one that was active when the token
was minted — there is no equivalent of the `X-Workspace-Id` header here. The
role comes from the owner's membership, live on every request: demote them to
viewer and their token stops being able to write, revoke their membership and
the token stops working at all.

## Authentication

`Authorization: Token <pat>` or `Authorization: Bearer <pat>` — both accepted,
same as the REST surface. Everything else is refused:

| Sent | Result |
|---|---|
| No `Authorization` header | `401`, JSON-RPC error `-32001` |
| Unknown / malformed / revoked / expired token | same `401`, byte for byte |
| Owner no longer a member of the workspace | same `401` |
| A valid session cookie and nothing else | same `401` |

The single body is deliberate (ADR-0029): nothing here distinguishes "no such
token" from "revoked", so a stolen token cannot be probed for why it failed.

**The session cookie is not a credential for this surface.** That is what lets
`/mcp` sit outside the CSRF Origin guard — the guard defends cookie
authentication, and there is none to defend. `backend/tests/test_mcp.py`
pins it.

## Tools

19 tools — 10 read, 9 write. Names and arguments are the agent-facing contract;
the authoritative descriptions are the docstrings in `backend/app/mcp/tools/`,
which are what the model actually reads.

Most `part_id` arguments accept **either** a part id or an exact MPN, because
an assistant reading a schematic has one and an assistant following up on an
earlier result has the other.

### Read

| Tool | Answers |
|---|---|
| `search_parts` | Free-text search over name, MPN, manufacturer, IPN, description |
| `get_part` | One part: specs, catalog metadata, stock by location, CAD status |
| `get_part_eda` | A part's KiCad configuration, with resolved `PCM_SM_…` refs |
| `find_parts_missing_eda` | "What still needs a footprint?" — by `symbol`/`footprint`/`model3d`/`spice` |
| `stock_levels` | On-hand / reserved / available, one part or the whole inventory |
| `list_storage_locations` | Bins and their constraints |
| `list_categories` | Categories and their slugs |
| `list_projects` | Projects (boards) |
| `get_project_bom` | One project's BOM lines |
| `bom_shortages` | What you are short of to build N boards |

### Write

| Tool | Does |
|---|---|
| `sourcing_offers` | Distributor price and stock for a part (see below) |
| `set_part_eda` | Replace a part's KiCad configuration |
| `upload_eda_asset` | Add a symbol / footprint / 3D model / SPICE file (base64) |
| `import_vendor_zip` | Import a SnapEDA / SamacSys / UltraLibrarian archive (base64) |
| `fetch_lcsc` | Fetch and convert CAD data from LCSC / EasyEDA |
| `add_stock` | Add stock |
| `consume_stock` | Consume stock |
| `move_stock` | Move stock between locations |
| `create_category` | Create a part category |

## Permissions

Two independent gates, both checked before a write tool's body runs:

1. **`read_only` on the token.** A credential-level rule — this is the token
   you paste into a KiCad config file, and its exposure must not cost you any
   writes.
2. **The owner's role.** A viewer's token is viewer-powered however it was
   minted. Writes need `member` or above, the same floor
   `require_member_for_writes` applies to the REST routers.

### Why a price lookup counts as a write

`sourcing_offers` answers a read-shaped question and is nonetheless in
the write column, so a `read_only` token and a viewer are both refused
it. The flag follows the **cost**, not the shape of the answer: a lookup
that misses the cache reaches TrustedParts over the network, spends a
slice of the workspace's metered distributor quota, and writes a
`sourcing_cache` row. The REST twin agrees — `POST /api/sourcing/search`
sits behind `require_role("member")`.

The trade is deliberate and it is a real loss: the credential you paste
into a KiCad config file can no longer ask what a part costs. That is
the price of it also being unable to spend your quota if the file leaks.

### Read-only tokens

A read-only token **connects normally and sees the full tool list**, including
the write tools. It is refused only when it calls one:

```
auth.token_read_only: this tool writes and the token is read-only;
mint a full-access token to use it
```

Listing them is the deliberate choice. Hiding the write tools would teach the
assistant they do not exist, and it would then confidently tell the user the
feature is missing rather than that the credential is wrong.

## Errors

A failing tool returns an MCP tool error whose text starts with the app's own
error code — the same stable string the REST surface puts in `status.category`:

```
part.not_found: no part in this workspace matching 'STM32G071'
stock.operation_error: insufficient stock (have 3, want 10)
resource.insufficient_role: this tool writes and requires role member+ in this workspace
```

The code leads because it is the half that does not get reworded. A stack trace
is never returned; an unexpected exception becomes a generic
`Error executing tool <name>` and is logged server-side with its traceback.

Cross-workspace ids are `*.not_found`, never a permission error — same rule as
the REST surface ([ADR-0002](../adr/0002-code-enforced-workspace-isolation.md)).

## Limits

| Limit | Value |
|---|---|
| Decoded size of any base64 tool argument | 4 MiB |
| `search_parts` results | 50 |
| `find_parts_missing_eda` results | 100 |

Listings that hit their cap return `truncated: true`. Narrow the query rather
than raising the limit — a hundred parts of context makes an assistant worse at
the task, not better.

Upload tools run the same validation lane as `POST /api/eda/*`: symbols and
footprints are parsed and re-emitted canonically, STEP and WRL files are checked
for their magic bytes, and a multi-symbol library is refused (use
`import_vendor_zip`).

## Audit

Every mutation writes the same `audit_log` row the equivalent REST route writes
— same action name, same comment grammar — attributed to the **token's owner**.
An agent is not a principal here; it is a person's credential acting on their
behalf, and the trail names someone who can be asked about it.

Stock movements are the exception, and match the REST path: no audit row,
because the `stock_entries` ledger row *is* the record and carries its own
`created_by`.

## Disabling

`MCP_ENABLED=false` in the backend environment removes the mount entirely, so
`/mcp` 404s like any unrouted path. It is the kill switch for an agent
integration that needs stopping without redeploying the app.

## See also

- [agents](agents.md) — driving the REST API directly with the same token
- [tokens](tokens.md) — minting, revoking, the token model
- [ADR-0030](../adr/0030-mcp-server-surface.md) — why in-process, why stateless, why the service layer
- [ADR-0029](../adr/0029-api-tokens-and-csrf-exemption.md) — the credential and the CSRF exemption
- `backend/app/mcp/README.md` — the module map
- `backend/tests/test_mcp.py` — this page, executable
