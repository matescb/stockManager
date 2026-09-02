# Phase 14 — KiCad libraries and the agent API

Audience: engineer

Why the workspace grew a CAD library, why KiCad talks to it over two
protocols instead of one, and why the same credential opens a REST
surface and an MCP server. Nine PRs, two ADRs, one migration chain
(`0067` → `0069`).

## Why

- A parts database that does not reach the schematic editor is a
  second place to type the same MPN. The engineer's actual workflow
  starts in KiCad, so the inventory has to appear *there* — in the
  symbol chooser — or it does not get used.
- Vendor CAD archives (SnapEDA, SamacSys, UltraLibrarian, LCSC) are
  the normal way a symbol enters a project. Downloading a zip,
  unpacking it into a personal library folder and remembering which
  part it belonged to is exactly the bookkeeping this app exists to
  remove.
- The same reasoning applies one layer up. An assistant asked "do we
  have 100 of these in stock, and does the BOM clear?" should not have
  to be handed a REST reference first.

## The two-channel architecture

KiCad cannot get what it needs from one endpoint, and the split is not
ours to choose.

**Channel one — the HTTP library** (`/kicad-api/v1`, phase 5). KiCad's
`kicad_httplib` protocol: `GET`-only, `Authorization: Token …`, fixed
raw-JSON documents in which every scalar is a string. It serves
*metadata* — a part's name, its `symbolIdStr`, its fields, its
footprint filters. It is explicitly **not** a way to ship files: the
`symbolIdStr` it returns must already resolve in the user's local
libraries or KiCad reports a broken symbol.

**Channel two — the PCM repository** (`/kicad-api/pcm/{token}`, phase
6). KiCad's Plugin & Content Manager: a `repository.json` pointing at a
`packages.json` pointing at a `package.zip`, from which KiCad installs
the actual `.kicad_sym`, `.pretty` and 3D files. It is the only
mechanism KiCad has for *installing* library content, and it is a
package manager, so its unit of change is a version bump the user
accepts.

Neither channel knows about the other. What ties them together is a
string, and getting that string right in two places independently is
the failure mode the whole design guards against — see
[the naming contract](#the-naming-contract).

Full protocol reference: [api/kicad](../api/kicad.md).

## What shipped

Nine PRs, each one merge:

1. **`part_categories`** (`0067`) — categories carry the per-category
   KiCad defaults (refdes prefix, symbol/footprint refs, footprint
   filters) and the `library_slug` every generated library name is
   built from. `parts.category_id` gets a BEFORE trigger checking the
   workspace — the second DB-enforced isolation rule, after `0036`.
2. **The EDA domain** (`0068`) — `eda_symbols`, `eda_footprints`,
   `eda_datafiles`, `eda_footprint_models`, `part_eda`; the text-CAD
   storage lane; the in-house s-expression tokenizer; the CAD tab. See
   [domain/eda](../domain/eda.md).
3. **The import pipeline** — vendor-zip detection and the LCSC fetch,
   split into a plan builder that touches neither disk nor database
   and a writer that owns both.
4. **Personal access tokens** (`0069`) — the non-cookie credential,
   with the `read_only` flag that phase 6 depends on. [ADR-0029](../adr/0029-api-tokens-and-csrf-exemption.md).
5. **The KiCad HTTP library** — channel one, plus
   `GET /api/eda/kicad-setup` and the `.kicad_httplib` file it
   describes.
6. **The PCM repository** — channel two: package building, the
   content-addressed zip cache, the version derivation.
7. **Agent REST enablement** — a token smoke test across the whole
   surface, and [api/agents](../api/agents.md).
8. **The MCP server** — mounted in-process at `/mcp`, same credential.
   [ADR-0030](../adr/0030-mcp-server-surface.md).
9. **The setup page and this documentation shelf.**

## Decisions worth knowing

### The naming contract

`backend/app/domain/eda/kicad_refs.py` is a module whose entire job is
to be imported twice. Phase 5 serves `PCM_SM_<slug>:<entry>` strings;
phase 6 generates the library files those strings name. A one-character
disagreement between them is not a subtle bug — it is a broken symbol
on **every part in the workspace**, reported by KiCad with no clue as
to which side is wrong. Neither side formats its own strings.

**The `PCM_` prefix is not ours.** KiCad's Plugin & Content Manager
prepends it when it registers an installed package's libraries. We
cannot opt out and cannot configure it; we can only predict it, which
means phase 5 has to emit a prefix that phase 6 never writes anywhere.
That asymmetry is the single most surprising thing in the contract, and
it is why the module carries the forum thread and the
`PCM_LIB_TRAVERSER` citation in its docstring.

**`SM_` is ours**, and namespaces every generated library away from a
stock KiCad one or one the user installed themselves.

**The slug comes from the entry's own category, not the part's.** A
symbol filed under *Resistors*, used by a part filed under *Passives*,
is `PCM_SM_resistors:…`. Getting this backwards produces references
that are individually plausible and collectively wrong.

### Read-only tokens, and why the rule is in the URL

The PCM sends no `Authorization` header — not for the repository, not
for `packages.json`, not for the archive. So the credential has to ride
the URL path, which is the one place a bearer token is most likely to
leak: an access log, a wiki page, a screenshot, a Sentry event.

The mitigation is to make what leaks worth less. `/kicad-api/pcm/`
accepts `read_only` tokens **and nothing else**; a full-parity token
presented there is refused with the same 404 as a revoked one. The
check runs *after* authentication so the attempt still lands in
`last_used_at` — someone probing with a stolen full-parity token is
exactly what that column exists to make visible.

Two supporting pieces: `core/responses.py::mask_credential_segment`
masks the token segment before the app's own error log or Sentry sees
it, and the `read_only` flag itself is enforced at the single
`get_current_user` choke point rather than per route.

### Blocked surfaces

A token is not a skeleton key. What it deliberately cannot do:

- **Write, if it is `read_only`** — refused before the route runs, for
  every non-`GET`.
- **Exceed its owner's role.** A token acts as the user who minted it;
  a viewer's token cannot write however it is flagged.
- **Cross a workspace.** A token belongs to one workspace, and stops
  authenticating the moment its owner's membership ends.
- **Reach a session-only route.** Anything that manages credentials or
  membership stays cookie-authenticated — see
  [tokens § session-only routes](../api/tokens.md#session-only-routes).
- **Be recovered.** Only the HMAC is stored. A lost token is revoked
  and replaced, never read back.

The MCP surface inherits all of it and adds one bound of its own: it
refuses cookie authentication outright, which is why it can sit outside
the CSRF Origin guard without that being a shortcut.

### One 404 for everything

Both KiCad surfaces answer `404 kicad.not_found` to a bad token, an
unknown category, an ineligible part and a malformed UUID alike. KiCad
accepts nothing but a `200`, so distinguishing them buys the client
nothing and buys an attacker an oracle. The one deliberate exception is
`429`: it is raised before any router code runs, needs no valid
credential to reach, and flattening it would cost the caller its
`Retry-After`.

### Why the MCP server is in-process

It shares the services, the session factory, the audit log and the
credential with the REST app. A sidecar would have needed its own copy
of all five. It is mounted as pure-ASGI middleware rather than a
Starlette `Mount`, because `Mount("/mcp", …)` does not match `/mcp`
itself — only `/mcp/…` — and MCP clients POST their JSON-RPC body
without following the 307 that would result. `MCP_ENABLED=false`
removes the middleware entirely, which is what makes the kill switch
total: the path 404s like any unrouted one, with no hint a server was
ever there. [ADR-0030](../adr/0030-mcp-server-surface.md).

## The deploy trap this phase found

Phase 5 shipped, CI went green, and `/kicad-api/v1/` served the SPA
shell.

Two independent faults, both invisible:

**The stdin eater.** The deploy script arrives on `bash`'s stdin
through an SSH heredoc. A child process that reads stdin — `docker
compose exec` did — consumes the rest of the deploy script and the
session exits **0**. Half the deploy silently never ran, and GitHub
Actions reported success. Fixed by redirecting `< /dev/null` on every
child that could read stdin (`.github/workflows/ci.yml:1005-1010`).

**The stale web image.** `/kicad-api` is not under `/api`, so a web
container running an older nginx config had no route for it and fell
through to the SPA — answering `200 text/html`. That is the one failure
mode KiCad accepts and then chokes on, and no health check caught it,
because `/api/health` proved only the backend proxy path.

Two gates were added to the deploy job, both of which fail it loudly:

- **Health gate** — polls `/api/health` until `200`, ceiling 150 s,
  covering the rebuild plus `alembic upgrade` plus first-request
  warm-up.
- **Routing gate** — requires `/kicad-api/v1/` to answer with
  `Content-Type: application/json`, and dumps the live nginx `location`
  blocks when it does not (`ci.yml:1049-1080`).

`deploy/nginx-web.conf` now carries explicit `location` blocks for
`/kicad-api/`, `/catalog/`, `= /mcp` and `/mcp/`;
`tests/test_deploy_nginx_routes.py` reads the path constants out of the
app so the config and the code cannot drift.

## Trade-offs taken

- **No `kicad-cli` at request time.** A legacy KiCad 5 `.lib` is
  rejected with the exact upgrade command in the message rather than
  converted server-side. Converting would mean a subprocess, a
  writable working directory and an unbounded runtime on
  attacker-supplied input.
- **An in-house s-expression tokenizer**, not `kiutils`. The
  maintained candidate lags the format by a release or two and fails
  on nodes it has not been taught — the wrong behaviour for a user's
  upload, where the correct move is to preserve what you do not
  understand.
- **Orphan blobs are never swept.** A rename re-emits to a new content
  hash and the old file stays on disk, consistent with
  [ADR-0005](../adr/0005-content-addressed-assets.md).
- **The package version does not move when the workspace is renamed.**
  `workspaces` has no `updated_at`. Documents and archive still agree,
  but KiCad will not notice the new name until the next content
  change.
- **`unrestricted`, not `proprietary`, as the package licence.** The
  PCM's v1 schema closes `license` to a 90-value enum and rejects the
  whole document over one bad value. The obvious label silently
  stopped the repository from loading; the served bytes are now
  validated against the vendored schema in
  `backend/tests/fixtures/pcm.v1.schema.json`.

## References

- [api/eda](../api/eda.md), [api/kicad](../api/kicad.md),
  [api/agents](../api/agents.md), [api/mcp](../api/mcp.md),
  [api/tokens](../api/tokens.md)
- [domain/eda](../domain/eda.md), [domain/data-model](../domain/data-model.md)
- [user/kicad](../user/kicad.md) — the operator-facing version
- [ADR-0029](../adr/0029-api-tokens-and-csrf-exemption.md) — tokens and the CSRF exemption
- [ADR-0030](../adr/0030-mcp-server-surface.md) — the MCP surface
- Migrations `0067_part_categories.py`, `0068_eda_domain.py`, `0069_api_tokens.py`
