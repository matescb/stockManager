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

22 tools — 10 read, 12 write. Names and arguments are the agent-facing contract;
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
| `create_part` | Create a part, or report the one that already holds the MPN |
| `set_part_category` | File a part under a category |
| `set_part_specs` | Write a part's own specifications |

### Authoring a part

Arguments and refusals for these three are in [Required arguments and
refusals](#required-arguments-and-refusals); what follows is the part of the
contract a table cannot carry.

**A duplicate MPN is a success, not an error.** `POST /api/parts` answers 409
with `existing_id`; `create_part` answers `{"found_existing": true, "part":
{…}}` and writes nothing. An MPN names one part, so finding it is the right
answer — and a tool error would be indistinguishable, to a model, from a
malformed call. Read `found_existing` before telling the user you added
something. The MPN is stripped first, so `"  LM358DR "` finds the same part
`"LM358DR"` does.

**`set_part_specs` never overwrites provider-supplied values.** Rows a parts
provider owns come back under `skipped_provider_owned` and are left as they
are, including under `replace_missing`. The REST route does the opposite on
purpose: a person editing one value in the UI is deliberately taking ownership
of it and the row becomes an `override`. A bulk agent write is not deliberate
about any single row. Nothing on this surface changes a row's `source` —
`manual` rows stay manual, `override` rows stay overrides.

`replace_missing` deletes only plain `manual` rows the new payload does not
name — narrower than the set the tool may *write*, and deliberately so. An
`override` is the record that a person looked at a provider value and replaced
it, and its `original_value` is the only copy of what upstream said; deleting
it would throw away both, and the next provider refresh would restore the
upstream value as though the disagreement had never happened. So an override is
updatable and never deletable. Provider rows and the reserved keys below are
not the tool's at all.

Keys are compared exactly, so a key with leading or trailing whitespace is
refused rather than stored: `"Tolerance "` would otherwise sit beside the
provider's `"Tolerance"` as a second spelling of one specification, and
`"image_url "` would slip past the reserved list below, which is a literal
match.

Reserved keys — `image_url`, `datasheet_url`, `source_url`, and anything
prefixed `digikey:` or `mouser:` — are refused outright, and one bad key
refuses the whole call so a batch never lands half written
([ADR-0031](../adr/0031-primary-and-secondary-parts-providers.md) owns the namespaces).

## Workflows

The four sequences an assistant is actually asked for, as ordered tool calls.
Argument names are the ones in `backend/app/mcp/tools/`; they are not uniform
across the surface, so copy them rather than inferring them.

### Add a part from a manufacturer part number

There is **no provider-lookup tool on this surface.** Nothing here searches
DigiKey or Mouser by MPN and hands back attributes — the MPN, manufacturer,
description and specifications come from wherever the assistant already has
them (a schematic, a datasheet, its own knowledge). `sourcing_offers` is not
that tool either: it takes a `part_id`, so it can only run once the part
exists, and it answers price and availability rather than parameters.

1. `list_categories()` — once per session. You need a category id, name or
   slug for step 3, and the refusal in step 3 is cheaper to avoid than to
   recover from.
2. `create_part(mpn=…, manufacturer=…, description=…, category_id=…)`.
   `name` defaults to the MPN, so an MPN alone is a complete call.
   **Read `found_existing` before reporting success** — `true` means the
   workspace already had this MPN and nothing was written.
   `category_id` accepts an id, an exact name or a slug, so passing it here
   makes step 3 unnecessary.
3. `set_part_category(part_id_or_mpn=…, category_id_or_name=…)` — only to
   refile a part, or when step 2 ran without a category. Create a missing
   one with `create_category(name=…)` first.
4. `set_part_specs(part_id_or_mpn=…, specs={…})`. Values are stored verbatim,
   units included. On a part a provider already owns, read
   `skipped_provider_owned` in the result: those keys were not written.
5. `add_stock(part_id=…, qty=…, storage_location_id=…)` if you also hold
   quantities — `create_part` never sets stock.

Steps 2–4 are three writes, three audit rows and three chances to be refused.
That is deliberate: `create_part` takes no `specs` argument, because a
partially-valid batch would leave the model reconciling what landed.

### Add stock from a scan

Bag codes are not on this surface. The lookup lives on a REST route the web
scanner uses — `GET /api/parts/by-bag-signature/{signature}`
(`backend/app/api/routes/parts_core.py:516-517`) — so an assistant works from
the MPN a scanner decoded, not from the raw bag code.

1. `search_parts(query=<MPN>)` — confirm the part exists and get its id. Skip
   it and go straight to `create_part` if it may not exist yet; that call
   answers the "already there" case itself.
2. `list_storage_locations()` — `add_stock` takes a location **id**, and the
   response carries each location's constraints (`is_full`,
   `single_part_only`, `existing_parts_only`) so a refusal can be predicted.
3. `add_stock(part_id=…, qty=…, storage_location_id=…, note=…)`. Pass the
   location. Omitting it lands the stock in the unassigned pool, and is
   refused outright on a part with a mandatory default location.
4. The result already carries the part's new `on_hand`; a follow-up
   `stock_levels` call is redundant.

`consume_stock` mirrors this and has one trap: omitting `storage_location_id`
consumes from the **unassigned pool only**, not from wherever the part happens
to sit. Call `get_part` first and pass the location that actually holds the
stock. `move_stock(part_id, qty, from_location_id, to_location_id)` requires
both ends.

### Wire CAD data

1. `find_parts_missing_eda(kind="footprint")` — or `"symbol"`, `"model3d"`,
   `"spice"`. This is the work queue.
2. `get_part_eda(part_id=…)` — **mandatory before step 4.** `set_part_eda`
   replaces the whole configuration; an omitted argument is written as its
   default, not left alone.
3. Get the bytes into the library, by whichever door fits:
   - `import_vendor_zip(part_id=…, content_base64=…, overwrite=False)` for a
     SnapEDA / Component Search Engine / UltraLibrarian archive. It wires the
     part as it imports, so step 4 is often unnecessary.
   - `fetch_lcsc(part_id=…, lcsc_id="C25804", overwrite=False)` — same, with
     the bytes fetched from EasyEDA rather than uploaded.
   - `upload_eda_asset(kind=…, filename=…, content_base64=…, part_id=…,
     category_slug=…)` for a single file. With `part_id` it fills that part's
     **empty** slots only and reports `part_eda_updated`.
4. `set_part_eda(part_id=…, …)` for anything the importers did not set —
   `value`, `keywords`, `footprint_filters`, the `exclude_from_*` flags, or a
   reference into the user's own local KiCad libraries. A symbol is named by
   `symbol_id` (hosted here) **or** `symbol_ref_external` (`"Device:R"`),
   never both; same for `footprint_id` / `footprint_ref_external`. Leaving
   both empty inherits the category default. Pass back everything from step 2
   you want to keep.

### Check a BOM against stock

1. `list_projects()` — projects, newest first, with their ids.
2. `bom_shortages(project_id=…, build_qty=N)`. One row per line that cannot be
   covered, with `required`, `available` and `short_by`. Substitutes and
   meta-part members count towards availability; do-not-populate lines and
   lines with no linked part are skipped. An empty `shortages` list means the
   build is covered, and `lines_checked` says how many lines that verdict
   rests on.
3. `get_project_bom(project_id=…)` only if you need the lines themselves —
   `designators`, per-board `quantity`, `dnp`. The shortage answer does not
   require it.
4. Per shorted part: `stock_levels(part_id=…)` for the `on_hand` / `reserved`
   / `available` split, and `sourcing_offers(part_id=…, qty=…)` for price and
   distributor availability. `sourcing_offers` spends metered quota and is
   rate-limited to 60/minute — call it for the shortages, not for the BOM.

## Required arguments and refusals

Every tool can additionally return
`auth.token_read_only`, `resource.insufficient_role` or `rate_limited`; those
three are properties of the credential and the clock, not of the call, and are
not repeated per row.

| Tool | Required | Optional | Characteristic refusals |
|---|---|---|---|
| `search_parts` | `query` | `category_slug`, `limit` | `category.not_found` |
| `get_part` | `id_or_mpn` | — | `part.not_found` |
| `get_part_eda` | `part_id` | — | `part.not_found` |
| `find_parts_missing_eda` | `kind` | `category_slug`, `limit` | `category.not_found` |
| `stock_levels` | — | `part_id`, `low_stock_only`, `limit` | `part.not_found` |
| `list_storage_locations` | — | — | — |
| `list_categories` | — | — | — |
| `list_projects` | — | — | — |
| `get_project_bom` | `project_id` | — | `project.not_found` |
| `bom_shortages` | `project_id` | `build_qty` | `project.not_found` |
| `sourcing_offers` | `part_id` | `qty` | `part.not_found` only. Every other failure is **degradation, not a refusal**: a part with no MPN, unconfigured sourcing, an exhausted budget and a provider error all return `status` (`no_mpn` / `not_configured` / `budget_blocked` / `provider_error`) with an empty `offers` list. Read that as "unknown", not "unavailable" |
| `create_part` | one of `name` / `mpn` | `manufacturer`, `description`, `category_id`, `part_type`, `internal_part_number` | `part.name_or_mpn_required`; `category.not_found`; `category.archived`; `part.invalid_field` (over-long value). **Not** `part.mpn_conflict` — see below |
| `set_part_category` | `part_id_or_mpn`, `category_id_or_name` | — | `part.not_found`; `category.not_found` (lists up to 10 existing names); `category.name_conflict` when two differ only in case |
| `set_part_specs` | `part_id_or_mpn`, `specs` | `replace_missing` | `custom_field.reserved_key`; `custom_field.key_whitespace`; `custom_field.too_many` (> 50 keys); `custom_field.too_long` (key > 256, value > 1024) |
| `create_category` | `name` | `description` | `category.name_conflict`, `category.slug_conflict` |
| `add_stock` | `part_id`, `qty` | `storage_location_id`, `note` | `stock.operation_error` — a non-positive `qty`, a location that is archived, full or not in this workspace, or the mandatory-default rule below; `stock.constraint_violation` for `single_part_only` / `existing_parts_only` |
| `consume_stock` | `part_id`, `qty` | `storage_location_id`, `note` | `stock.operation_error` — a non-positive `qty`, or "insufficient stock (have 3, want 10)" |
| `move_stock` | `part_id`, `qty`, `from_location_id`, `to_location_id` | — | `resource.not_found` for either end; `stock.operation_error` for a non-positive `qty` or too little stock at the source; `stock.constraint_violation` on the destination |
| `set_part_eda` | `part_id` | the other eleven | `eda_symbol.not_found`, `eda_footprint.not_found`, `eda_datafile.not_found`; `eda.ref_conflict` when both `<slot>_id` and `<slot>_ref_external` are set; `eda.archived` for a retired entry |
| `upload_eda_asset` | `kind`, `filename`, `content_base64` | `part_id`, `category_slug` | `eda.unsupported_kind`, `eda.invalid_file`, `eda.empty_file`, `eda.file_too_large`, `eda.multiple_symbols`, `eda.legacy_format` |
| `import_vendor_zip` | `part_id`, `content_base64` | `overwrite` | `eda.invalid_archive`, `eda.archive_too_large`, `eda.no_entries` |
| `fetch_lcsc` | `part_id`, `lcsc_id` | `overwrite` | `eda.lcsc_not_found`, `eda.lcsc_unavailable` |

Three spellings of the same argument survive on this surface — `id_or_mpn`
(`get_part`), `part_id` (everything in `read.py`, `write.py`,
`write_inventory.py`, `sourcing.py`) and `part_id_or_mpn` (`write_parts.py`).
All three accept a part id **or** an exact MPN; only the names differ.

### The four refusals worth knowing before you call

**A duplicate MPN is not a refusal.** `create_part` on an MPN this workspace
already holds returns `{"found_existing": true, "part": {…}}` and writes
nothing — no part, no audit row. The REST twin answers `409` with
`existing_id`; the tool converts it. The MPN is stripped first, so
`"  LM358DR "` finds the part `"LM358DR"` names. Every other 409 (an archived
category, for one) stays a failure, because the conversion matches on the
error code rather than the status.

**A mandatory default storage location is enforced on `add_stock`.** A part
with `default_storage_mandatory` set and a default location configured refuses
any addition that does not name **that** location — including one that names
no location at all. The refusal is
`stock.operation_error: part requires default storage location`
(`backend/app/domain/stock/service.py:609-611`). `create_part` cannot set
either flag, so an assistant meets this only on parts a person configured.
Three more `stock.operation_error` cases share the shape: the location is
archived, marked full, or not in this workspace. Two others are the
workspace's serial-tracking rule — a serialized part must be added one at a
time and with a `lot.serial_number`, which this surface cannot supply.

**The workspace is pinned to the token and nothing can move it.** There is no
`X-Workspace-Id` equivalent here. An id belonging to another workspace is
answered `part.not_found` / `category.not_found` / `resource.not_found` —
never a permission error, the same rule the REST surface follows
([ADR-0002](../adr/0002-code-enforced-workspace-isolation.md)). An assistant
that needs two workspaces needs two tokens and two configured servers.

**A read-only token is refused at call time, not at list time.** The string is
fixed:

```
auth.token_read_only: this tool writes and the token is read-only;
mint a full-access token to use it
```

It applies to all twelve write tools, `sourcing_offers` included. The role
check runs second and answers
`resource.insufficient_role: this tool writes and requires role member+ in
this workspace` (`backend/app/mcp/tools/_registry.py:95-123`).

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
| `set_part_specs` keys per call | 50 |
| `set_part_specs` key / value length | 256 / 1024 characters |
| `create_part` `mpn` / `manufacturer` / `internal_part_number` | 200 / 200 / 120 characters |

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

`set_part_specs` has no REST twin — one call writes many rows — so it records
one `part.specs_updated` row naming the keys that moved and never their values.
A call that changed nothing writes none. `create_part` on an existing MPN
writes none either: nothing happened, and the trail must not say a part was
created.

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
- `backend/tests/test_mcp.py`, `backend/tests/test_mcp_parts.py` — this page, executable
