# Changelog

Through Phase 10, each phase corresponded to a single squashed commit +
a `docs/phases/NN-*.md` document. The post-Phase-10 stream below didn't
follow that model — it's a continuous flow of product work + production
hardening landing per-commit. Themes are summarised below; `git log` is
the canonical record.

## Breaking changes

- **AUD-121 / #812** `audit_log.comment` throttle rows changed from the
  legacy `throttled` literal to `throttled:rate` for rate-limit throttles
  and `throttled:concurrent` for concurrent-request throttles. Downstream
  consumers filtering audit logs by the old value must update their filters.

## Unreleased

- **`provider-refresh` re-asks the providers about parts imported before
  the importer knew what it knows now.** A new operator-run job sweeps
  every active, linked part with an MPN in a workspace and writes back
  what the providers answer today — part columns on the primary tier, a
  category where there was none, canonical specs, missing assets —
  through exactly the same `refresh_part` the refresh route calls, so
  the tier and namespace rules are decided once. It is `--dry-run` by
  default with the lookups done for real inside a rolled-back savepoint,
  sleeps 750 ms between calls, commits per batch of 25 parts, and stops
  at exit 3 when a provider reports it is out of quota with everything
  finished so far kept, and downloads no assets at all on a dry run. Every
  pair needs an EXACT MPN match, because DigiKey falls back to a keyword
  search and a near miss would write another product's specs onto the
  part. `--link-missing-providers` also asks the SECONDARY providers a
  part is not linked to; it never promotes one to primary, which would
  rewrite six part columns from a provider nobody chose for that part.
  `docs/runbooks/provider-refresh.md`; ADR-0021.
- **The refresh sequence moved out of the route.**
  `domain/parts/services/provider_refresh.py::refresh_part` is now the
  one implementation, and `POST /api/parts/{id}/refresh-from-provider`
  keeps only its status codes, envelope and rate limit. No response
  changed.
- **`run_job` grew a third exit code.** Exit 3 means "a job stopped on
  purpose and can be re-run later", which a runbook step has to tell
  from a clean finish and from a usage error. `JobSpec.extra_flags` now
  records, per flag, the value that means "not given": `--sleep-ms 0` is
  a real choice, so "not given" could no longer be spelled as "falsy".

- **The spec schema covers active components.** ICs, connectors, crystals,
  fuses, switches, transformers and mechanical parts had no canonical
  schema at all: every value on them was kept verbatim, so nothing sorted
  and no key could be reported missing. Seven slugs now name what each
  class is supposed to have, and `category-seed` grew the seven matching
  roots (21 seed rows to 28) with their reference designators, footprint
  filters and `kicad_fields`. Crystals and fuses get a stock
  `Device:Crystal` / `Device:Fuse`; ICs, connectors, switches,
  transformers and mechanical parts deliberately get none, because a
  default that is wrong for every part in the class is worse than falling
  through to the part's own symbol. Importing a DigiKey connector now
  files it under Connectors and normalises its pitch to `2.54 mm`.
- **Seven optional spec keys common to every category**: `height`,
  `length`, `width`, `pin_count`, `pin_pitch`, `automotive` and
  `device_marking`. Three of them read part of a value rather than all of
  it — a `Size / Dimension` of `0.126" L x 0.063" W (3.20mm x 1.60mm)`
  fills both the length and the width, dimensions prefer the metric
  equivalent the vendor printed next to the inches, and `Ratings` yields
  its AEC-Q qualification or nothing. `Ratings` and `Qualification` came
  off the junk denylist for that last one; a value with no AEC-Q token in
  it is still dropped rather than kept as prose.
- The value parser reads newtons, volt-amperes and cycle counts, and
  takes the leading term of a conditioned rating however the vendor
  separated it (`2A @ 125VAC`, `50mA at 12VDC`, `3A/250VAC`). A
  fraction and a slash inside a unit symbol are not conditions: `1/16W`
  is still a sixteenth of a watt and `100ppm/°C` is still one quantity.
- **`spec-normalize` gained an `add` action.** The schema's one-to-many
  alias needs an INSERT, not a second rename: `Size / Dimension` answers
  both `length` and `width` and a part carries one row for it. The first
  canonical key renames that row, the second gets a copy. Reversing an
  `add` line is a DELETE — see the runbook.

- **Part names now say what the part is.** `parts.name` is the canonical
  identity: a part whose category carries a `value_template` is named by
  that template behind the category's class letter (`R 10 kΩ 1% 0603`),
  and everything else by its MPN. The manufacturer has its own column
  and the supplier's copy stays in `description`, which is where the
  127 prod parts named `RES SMD 10K OHM 1% 1/16W 0603` got their name
  from. `domain/parts/naming.py` is the single definition; provider
  import and BOM auto-create apply it at creation, and a user-supplied
  name is never rewritten. Importing a resistor from DigiKey now files
  it under Resistors, normalises `Resistance: 10 kOhms` to
  `resistance: 10 kΩ` and names it `R 10 kΩ 1% 0603`, none of which is
  a string the provider sent. A template renders a name only when every
  one of its placeholders resolves — a partial render is what would name
  every half-specified resistor in a workspace `R 0603` — so a part whose
  specs fall short keeps its part number. A project's words for a BOM
  line stay on the line — `ProjectEntry.name` already carried the BOM's
  "part" column, so BOM auto-create simply stops copying it onto the part
  when the row has an MPN.
- **`run_job part-rename`** converts an existing catalogue, the fourth
  operator-run job: reporting by default, renaming only with `--apply`,
  which it refuses without `--report` because it rewrites `parts.name`
  in place. Two rules keep it from destroying text. A hand-typed
  (`free`) name is reported but not renamed unless `--include-free` is
  passed, being the one class an import never produced; and a part that
  already carries an `alias` custom field is skipped rather than
  renamed, since `uq_cf_unique` allows one row per key and the old text
  would have nowhere to go. Everything else parks its old name in a
  `manual` `alias`, including the `description` class — that column
  looks self-preserving and is not, being provider-owned on a linked
  part. The CSV says per row where the old name ended up and why
  anything was skipped. Re-running is a no-op, which matters because
  most parts reach only their MPN until `spec-normalize` has re-keyed
  their provider spec rows; those are reported as
  `template_unrenderable` and renamed the rest of the way on a later
  run. `--apply` writes one audit row per workspace carrying counts
  only. `--include-free` is the first flag only one job reads, declared
  through `JobSpec.extra_flags` and refused by name for every other job.

- **`spec-normalize` re-keys the specs that pre-date the schema.** A new
  one-off `run_job` backfill does in bulk what a refresh does one part at
  a time: it moves existing `custom_fields` rows onto their canonical key
  with a parsed value and a `value_num` sidecar, archives customs codes
  and `-` placeholders, stamps `custom_fields.provider` on every
  canonical row it writes, and files parts that have no category from
  their provider's taxonomy. It is `--dry-run` by default and writes
  nothing in that mode; the CSV it produces (per-change lines, counts per
  workspace, and the raw keys the schema had no alias for) is the review
  step before `--apply`. Nothing is ever deleted, `manual` and `override`
  rows are invisible to it, a category a user chose is never overridden,
  and a second run reports zero changes. `--apply` requires `--report`,
  because the CSV is the only record of the values it replaces.
  `--workspace` narrows it to one tenant. Runbook:
  `docs/runbooks/spec-normalize.md`; ADR-0034.
- **Provider specs are normalised on import and refresh, and imported
  parts get a category.** `domain/parts/services/spec_reconcile.py` is now
  the single writer for a provider payload, used by both the create path
  and the refresh route. Specs land under their canonical key with a
  parsed value and a numeric sidecar (`Resistance: "10 kOhms"` becomes
  `resistance: "10 kΩ"`, `value_num = 10000`); customs codes and `-`
  placeholders are never written and existing ones are archived; price,
  stock and packaging keep the exact keys and namespaces the Sourcing tab
  reads. **Both DigiKey and Mouser now write canonical specs**, so a
  workspace with two providers gets both their parametric data on one
  Specs tab — a contested key is resolved by precedence (DigiKey beats
  Mouser) recorded in `custom_fields.provider`, not by whoever refreshed
  last. A part with no category is filed from the provider's own
  taxonomy: `Chip Resistor - Surface Mount` → `Resistors`,
  `Ceramic Capacitors` → `Capacitors / Ceramic`, falling back to the root
  of the path and never creating a category. When nothing resolves, the
  response carries `category_suggestion`. Part detail and list rows gain
  `spec_incomplete` / `missing_specs` — the mandatory keys the part's
  category says it should have and nobody supplied. The refresh response's
  `summary` also carries `archived`, `restored` and `dropped`, so an API
  client can tell "the vendor sent it and we did not store it" apart from
  silence; the toast in the UI still reports added / updated / removed. See
  ADR-0034 and the A3 amendment to ADR-0031. The 9,377 existing rows are re-keyed by a
  part's next refresh, or in bulk by the `spec-normalize` backfill,
  which landed with it.
- **One symbol per class in the KiCad chooser, and a category tree to hang
  it off.** Every vendor zip a workspace imports ships its own schematic
  symbol, and the KiCad document prefers a hosted symbol over the category
  default — so eighty imported resistors meant eighty `R` symbols, all
  drawing the same box. Two operator-run jobs fix that, both dry-run by
  default. `category-seed` gives a workspace the passive tree (Resistors;
  Capacitors / Ceramic, Electrolytic, Tantalum, Film; Inductors / Power,
  Ferrite bead, Common-mode choke; Diodes / Rectifier, Schottky, Zener, TVS,
  LED; Transistors / BJT NPN, BJT PNP, MOSFET N, MOSFET P), each row
  carrying a `refdes_prefix`, a stock `Device:*` symbol reference,
  footprint filters, and the `value_template` / `kicad_fields` that render
  the schematic `Value` from canonical specs. Pointing at KiCad's own
  `Device` library means no symbol bytes ship for a passive at all.
  `symbol-collapse` then clears `part_eda.symbol_id` on exactly the parts
  whose symbol came from a vendor zip and whose category now has a default
  to fall back on. Neither job renames, re-parents, overwrites a value a
  user set, or deletes anything: a collision is reported, not resolved.
  `run_job` grew `--dry-run` (the default), `--apply`, `--workspace` and
  `--report <path>`; a dry run ends in ROLLBACK and still writes the
  report, and a scheduled job handed those flags exits 2 rather than
  ignoring them. `docs/domain/categories.md` is the new page.

- **The manual now has a page on connecting an AI assistant.**
  `docs/user/agents.md` ships in `/help` and covers what an assistant can
  do with a workspace, minting the token, the client config snippet, the
  read-only versus full-access trade, and the refusals that read as
  failures but are not — a duplicate part number comes back as the
  existing part, a part with a mandatory storage location refuses stock
  anywhere else, and a token cannot leave the workspace it was minted in.
  The first troubleshooting step for a rejected connection is the
  `${STOCKMANAGER_TOKEN}` pitfall: many clients send header values
  verbatim and never expand the variable. Linked from the manual index
  and from the **Not KiCad — AI agents** card in **Settings → KiCad
  setup**.
- **`docs/api/mcp.md` gained Workflows and a required-arguments table.**
  Four ordered call sequences (author a part from an MPN, add stock, wire
  CAD data, check a BOM) and one table of every tool's required and
  optional arguments with the refusals each one actually raises. Two
  things the page previously left implicit are now stated: there is no
  provider-lookup tool on this surface, so `create_part` is the first
  call and not the second; and `get_part_eda` must precede `set_part_eda`,
  which replaces a whole configuration and writes every omitted argument
  as its default.
- **The MCP session `instructions` now brief the model on call order**
  rather than only on the tool inventory, and name every write tool.
  `tests/test_mcp.py` pins both — a renamed or added write tool that
  never reaches the briefing fails the suite — and caps the length, since
  this string is paid for on every session.
- **MCP: an assistant can now create a part.** Three write tools —
  `create_part`, `set_part_category`, `set_part_specs` — close the gap
  that made the server read-mostly for anything starting from a
  schematic: there was no way to add a part, file it, or record what it
  is. `create_part` turns the REST surface's duplicate-MPN 409 into a
  success carrying the part that already holds the MPN, because "it is
  already there" and "your call was wrong" have to be distinguishable to
  a model. `set_part_specs` writes `manual` custom-field rows and never
  touches ones a parts provider owns, reporting those back under
  `skipped_provider_owned`; reserved and `digikey:`/`mouser:`-prefixed
  keys are refused (ADR-0031). The create rules moved out of
  `api/routes/parts_core.py` into
  `domain/parts/services/create_part.py` so the route and the tool
  cannot drift on the name/MPN defaulting, the MPN pre-check or the
  workspace checks on the supplied category and storage ids.
- **`parts.mpn`, `manufacturer` and `internal_part_number` are now
  length-checked by the schema**, on create and on patch, at the column
  widths (200 / 200 / 120; `name` was already capped on create but not
  on patch). An over-long value used to reach Postgres and come back a
  `DataError` — a 500 for what is plainly a bad request.
- **`part_type` now follows the provider link** (`0080`). The column was
  written once, at creation, and never again — so a part created `local`
  that a supplier lookup later linked kept saying `local`, and the type
  pill renders the raw column. 160 of 324 prod parts were in that state.
  `domain/parts/part_type.py` re-derives `linked` / `local` from
  `linked_provider` at both transitions (the primary branch of
  refresh-from-provider, and the unlink PATCH) and writes one
  `part.type_synced` audit row per change; migration `0080` fixes the rows
  that already drifted. `meta` and `sub_assembly` are user-declared roles
  and are never rewritten, even on a part that carries a provider link.
  The type pill in the part header, the parts list and the list preview
  now names the provider too (`linked · DigiKey`) through one shared
  helper, `web/src/lib/partType.ts`; the parts-list column still sorts,
  searches and exports on the raw value. The downgrade is a deliberate
  no-op — the old values were wrong and unrecorded.

- **Canonical spec schema for provider specs (foundation, no behaviour
  change yet).** Provider specs are stored verbatim, so the Specs tab
  carries TARIC and CNHTS customs codes, ~1,000 rows whose value is
  literally `-`, and three spellings of the same resistance. New
  `domain/parts/spec_schema.py` names the canonical specs per category
  (resistor, ceramic/electrolytic/tantalum/film capacitor, inductor,
  diode/schottky/zener/TVS, LED, BJT, MOSFET), the DigiKey and Mouser
  field names that feed each one, the junk denylist and the
  catalog-vs-spec boundary; `spec_values.py` turns `10 kOhms`,
  `0.063W, 1/16W` and `26mOhm Max` into a base-unit number plus one
  canonical display string. Migration `0081` adds nullable
  `custom_fields.provider` and `custom_fields.value_num`. Nothing calls
  the new code yet — import and refresh are wired up in a follow-up.
  ADR-0034.
- **A KiCad symbol's `Value` can now come from the part's specs.** Most of
  the library is imported, and an imported part is named after the
  provider's description — so the schematic drew
  `RES SMD 10K OHM 1% 1/16W 0402` where an engineer expects `10 kΩ 1% 0603`.
  Part categories gain `value_template` and `kicad_fields` (migration
  `0082`): `{resistance} {tolerance} {package}` renders the Value from the
  part's own custom fields, and listed spec keys are also emitted as hidden
  symbol fields (`voltage_rating` → `Voltage Rating`). A hand-typed
  `part_eda.value` still wins, and a category with neither column set
  behaves exactly as before. The rendered value joins `keywords`, so the
  symbol chooser finds the part by what it is. Both columns are
  NULL-means-inherit up `parent_id`. Set them in Settings → Categories.
  Nothing is seeded; the PCM package is unchanged (it ships library
  entries, not parts, so `PACKAGE_FORMAT` did not move).
- **KiCad's category list now shows the tree.** A subcategory is named by
  its full path — `Capacitors / Ceramic` — and the rows come back
  depth-first. The httplib category document is `{id, name, description}`
  with no field for a parent, so a nested library was previously a flat
  list of leaf names.
- **PCM package: 3D models linked on the CAD tab now ship in the
  footprint.** Linking a STEP or WRL to a footprint wrote a join row
  only, so the packaged `.kicad_mod` carried no `(model …)` node and
  KiCad showed no model for footprints linked by hand (the zip importer
  already wrote the node). The build appends a node for every linked
  model the bytes don't already name. Package format 3, so installed
  copies are offered the update.
- **Datasheet fetches now send a User-Agent** (ADR-0033 postscript). The
  first production backfill stored zero of 249 external datasheets: httpx
  identifies as `python-httpx/…` and Akamai-fronted vendor origins answer
  403. Measured across 8 vendor hosts, the pinned IP-literal request shape is
  **not** the problem — it gets byte-identical results to a plain hostname
  request everywhere, so the DNS-rebinding protection stays untouched.
  `ASSET_FETCH_USER_AGENT` defaults to the crawler-convention
  `Mozilla/5.0 (compatible; stockmanager-datasheet-fetcher/1.0; +$APP_BASE_URL)`.
  Also: connect and read timeouts are now separate (10s / 20s), the
  wall-clock budget is 45s (batch size 12 → 10 to stay inside `timeout 600`),
  and a per-run per-host failure breaker stops one refusing vendor burning
  the retry budget of every part that cites it — it records one
  `part.datasheet.host_blocked` audit row instead. Permanent failures (404,
  410, non-HTTPS, non-PDF, oversize) now retire a row immediately instead of
  being retried for five days; 403/429/5xx/timeout stay retryable.

- **Local datasheet store** (`0079`, ADR-0033) — datasheets are now
  downloaded into the content-addressed asset store and registered as
  `attachments` rows (`file_type='datasheet'`), instead of being hot links
  to a manufacturer site. **Security-relevant:** the SSRF host allow-list in
  `domain/parts/services/assets.py` no longer applies to datasheets fetched by
  the cron backfill — the relaxation needs an explicit `allow_any_host=True`
  that no request handler passes, so nothing a user can trigger reaches a
  non-allow-listed host. It was blocking essentially every real datasheet —
  249 of 257 prod datasheet URLs live on 40+ manufacturer domains and only 7 were
  allow-listed, so 5 PDFs had ever landed on disk. Images keep the
  allow-list unchanged. What replaces it: the hostname is resolved **once**
  and the request is issued against that validated IP literal (`Host:` and
  TLS SNI/cert verification keep the real name), closing the
  DNS-rebinding window the old resolve-then-reresolve check left open;
  plus HTTPS-only, `follow_redirects=False`, no `user:pass@` URLs, the
  10 MB streaming cap, magic-byte validation, PDF-only for datasheets, and
  a per-host throttle. Read ADR-0033 before touching any of it.
  New `part_datasheets` table carries the fetch bookkeeping (`status`,
  `attempts`, `failure_code`) plus a `derived` JSONB slot so the planned
  Datalab markdown/JSON conversion needs no further migration. New
  `datasheet-backfill` job runs in a new `backend-cron-datasheets` sidecar
  (ADR-0021) — resumable, idempotent, `DATASHEET_BACKFILL_INTERVAL_SECONDS=0`
  disables it.

- **PCM package: symbol `Footprint` fields are re-pointed at packaged
  footprints.** Symbols imported from a vendor library kept the vendor's
  footprint nickname (`NSW:…`), which the installed package never
  registers, so every placed symbol reported a missing footprint. The
  build now rewrites the field to `PCM_SM_<slug>:<entry>` when the entry
  is a footprint the package ships; references to anything else are left
  as stored. The package version's major is now `pcm.PACKAGE_FORMAT` (2),
  so already-installed copies are offered the update.
- **More selectable columns on the parts list** — the `/parts` table grew
  from nine defined columns to eighteen while the seven it *shows* by default
  are unchanged: every new column is **hidden by default** and picked from the
  "Columns" menu. Nine new ones: internal P/N, available, low-stock threshold,
  provider, distributors, published, serialized, last refresh, and last change —
  alongside the category column (full tree path, resolved client-side against
  the list the rail already fetches). `GET /api/parts` list rows gained
  `updated_at` (the mixin-maintained "last change" — no migration) and
  `provider_links`, the latter loaded in **one batched query per page** rather
  than per row. `[]` on a list row means "no distributor knows this part";
  responses that never load the links (create-part) still omit the key.
  No schema change.
- **Hierarchical part categories** (`0078`) — `part_categories.parent_id`,
  a self-referencing FK with `ON DELETE SET NULL` and a
  `part_categories_parent_workspace_check` BEFORE trigger (SQLSTATE
  `WS001`). Categories now form a KiCad-library-style tree: a rail on
  `/parts` filters the list (`?category=<id>`, deep-linkable), and
  `GET /api/parts` gains `category_id` + `include_descendants` (default
  **true** — clicking a branch node includes everything filed beneath it).
  Cycles, self-parents and a 6-level depth cap are enforced in Python
  (`domain/categories/tree.py`) rather than by a recursive CTE, which this
  repo still has none of. **Archiving or deleting a mid-tree category
  promotes its direct children to the root; it does not cascade** — the
  confirm dialog names them.

## 2026-09 — KiCad libraries and the agent API

Nine PRs, migrations `0067`–`0069`. Rationale in
[`docs/phases/14-kicad-and-agent-api.md`](docs/phases/14-kicad-and-agent-api.md).

- **Part categories** (`0067`) — a workspace-scoped grouping carrying the
  per-category KiCad defaults (refdes prefix, symbol/footprint refs,
  footprint filters) and the `library_slug` every generated library name
  is built from. `parts.category_id` is guarded by a BEFORE trigger, the
  second DB-enforced workspace-isolation rule.
- **The EDA domain** (`0068`) — `eda_symbols`, `eda_footprints`,
  `eda_datafiles`, `eda_footprint_models` and `part_eda`; a separate
  text-CAD storage lane (the attachment magic-byte allow-list is
  unchanged); an in-house s-expression tokenizer; the part **CAD** tab.
- **Vendor and LCSC import** — SnapEDA, Component Search Engine and
  UltraLibrarian zips are detected by layout and imported whole; LCSC
  part numbers are fetched and converted through `easyeda2kicad`. A bad
  member is a skip note, not a failed import. Legacy KiCad 5 `.lib`
  libraries are refused with the `kicad-cli` upgrade command in the
  message.
- **Personal access tokens** (`0069`) — the non-cookie credential for
  KiCad, scripts and agents, with a `read_only` flag enforced at the
  single auth choke point. [ADR-0029](docs/adr/0029-api-tokens-and-csrf-exemption.md).
- **KiCad HTTP library** (`/kicad-api/v1`) — the `kicad_httplib`
  protocol: `GET`-only, raw JSON outside the app envelope, one
  indistinguishable 404 for every failure. Plus
  `GET /api/eda/kicad-setup` and the generated `.kicad_httplib` file.
- **KiCad PCM repository** (`/kicad-api/pcm/{token}`) — a per-workspace
  add-on package serving the library files the HTTP library only names.
  The credential rides the URL because the Plugin & Content Manager
  sends no headers, so **only `read_only` tokens are accepted there**;
  archives are byte-deterministic and content-addressed on disk.
- **Agent REST enablement** — token auth across the whole `/api`
  surface, with [`docs/api/agents.md`](docs/api/agents.md) as the entry
  point for non-browser clients.
- **MCP server at `/mcp`** — mounted in-process, same credential, named
  tools over the same services. `MCP_ENABLED=false` unmounts it
  entirely. [ADR-0030](docs/adr/0030-mcp-server-surface.md).
- **KiCad setup page** (`/settings/kicad`) — builds the
  `.kicad_httplib` download, the PCM repository URL and the SPICE path
  variable from a token pasted in the browser. The plaintext never
  returns to the server.
- **About page and in-app manual** (`/about`, `/help`) — the frontend and
  backend build identifiers side by side (both the deploy's 12-char git
  SHA, so a half-applied deploy shows up as a mismatch), the top of this
  file as "Latest changes", and the `docs/user/` shelf rendered inside the
  app. `GET /api/version` is the backend half; the manual is inlined into
  the bundle at build time by `web/scripts/copy-docs.mjs`.
- **Deploy gates** — the deploy job now fails loudly on a stale web
  image: a health gate polling `/api/health`, and a routing gate
  requiring `/kicad-api/v1/` to answer JSON rather than the SPA shell.
  A `< /dev/null` on every deploy child that could read stdin fixes an
  SSH heredoc consuming the rest of the script and exiting green.

## 2026-05 — feedback brief fixes

- **E2E-1 / #686** Playwright E2E now has smoke/core/nightly project
  tiers, shared authenticated fixtures and seed/mock helpers, an advisory
  label-gated `playwright-core` CI job, and a scheduled nightly workflow.
- **SA-2b / #538** Refresh now prunes overrides whose target offer disappeared
  upstream; info toast surfaces the count.
- **SA-10b / #539** Convert-orders route now uses explicit `raise_http` +
  `ErrorCodes.*` instead of the legacy `_error_response` mapper.
- **SA-8b / #535** Split PurchasePlanReviewPage to keep sourcing files under 300-LOC headroom cap.
- **SA-MED / #512** Sourcing cleanup tightened service-layer workspace
  guards, archived-project refresh filtering, raw Decimal wire prices, hashed
  TrustedParts user-agent workspace identifiers, budget-counter locking, and
  removed the deprecated `est_purchase_cost` sourcing capacity alias.
- **SA-12 / #504** Sourcing alerts now return `{ items, total, limit, offset }`
  with 50-row default pagination and frontend next/previous controls.
- **SA-13 / #505** Purchase-plan conversion is now capped at 10
  conversions/minute per workspace, and sourcing distributor filters reject
  requests with more than 25 values before provider fanout.
- **SA-14 / #506** Sourcing alert evaluation now batches identical
  workspace-scoped TrustedParts queries by canonical query hash.
- **SA-19 / #511** Workspace sourcing country and currency defaults now use
  active-list selects, with backend validation for non-active codes.
- **SA-17 / #509** Production cron sidecar jobs now have a 600-second
  per-run timeout that logs exit 124 on timeout while preserving cadence.
- **SA-15 / #507** TrustedParts, Mouser, and DigiKey outbound calls now share
  bounded retry backoff for 429/503 and transient connect/read timeout failures.
- **SA-18 / #510** Project Sourcing Lifecycle, Supply chain, and RoHS risk
  pills now include hidden Lucide icon prefixes so severity is not conveyed by
  colour alone.
- **SA-11 / #503** Sourcing alert create requests now validate threshold shape
  from the parent `alert_type`, returning 422 field errors for malformed
  thresholds before an alert can be persisted.
- **SA-16 / #508** Sourcing cache keys now include full workspace/provider
  request shape and TrustedParts credential rotation purges matching cache rows.
- **SA-10 / #502** Sourcing route-mapped failures now include stable
  top-level `code` discriminators, and the frontend switches on those codes
  for rate-limit, stale-plan, and currency-mismatch UX.
- **SA-8 / #499** Decompose ProjectSourcingPage into a feature folder.
- **SA-6 / #497** Project Sourcing now runs the audited BOM sourcing POST only
  from an explicit Source click, preserving the display cache without
  remount/focus/filter-change refetches.
- **SA-9 / #500** Purchase plan review now stores plan snapshots in a
  TanStack Query cache keyed by plan id, with direct-link reload hydration.
- **SA-1 / #492** Sourcing capacity now treats mixed-currency BOM totals as
  unknown instead of silently using the first currency.
- **SA-2 / #493** Fix PurchasePlanReviewPage refresh silently wiping user
  overrides; add error toast on failure.
- **SA-3 / #494** Sourcing alert notifications now commit `last_notified_at`
  before SMTP dispatch, trading one missed outage email for duplicate suppression.
- **SA-7 / #498** Project Sourcing modals now share an accessible dialog shell
  with focus trap, ESC close, backdrop dismiss, and focus restoration.
- **SX-10 / #485** Project Sourcing capacity now shows cost per single BOM
  alongside total BOM cost and short-quantity price to pay.
- **SX-12 / #487** Project Sourcing now uses the four-level TrustedParts
  risk vocabulary, including a light-green Low-Med band and header popover
  legends for Lifecycle and Supply chain risk columns.
- **SX-11 / #486** Project Sourcing coverage variant prices now represent the
  purchasable covered lines for the returned distributor combo, including partial
  coverage, and the UI labels partial totals as covered-line prices.
- **SX-5 / #480** Project Sourcing keeps the legacy distributor coverage matrix
  shortfall-based while the fewest-distributors variant continues to apply MOQ
  selected quantities for feasibility and totals.
- **SX-6 / #473** Project Sourcing BOM coverage now keeps TanStack Query
  display data warm for instant remounts and shows a non-blocking background
  refresh hint instead of a skeleton during refetches.
- **SX-5 / #472** Project Sourcing coverage now shows lowest-price and
  fewest-distributor combination cards above the per-distributor matrix.
- **SX-3 / #470** Project Sourcing BOM rows now open a TrustedParts distributor
  drill-down with availability text, price breaks, MOQ, packaging, RoHS data, and
  distributor links.
- **SX-4 / #471** Project Sourcing capacity now separates total BOM cost
  from the short-quantity price to pay, with `est_purchase_cost` retained as
  a deprecated alias.
- **SX-1 / #468** Project Sourcing now splits BOM lifecycle,
  supply-chain, and RoHS details into dedicated columns, hides the crowded
  lead-time column by default, and colours TrustedParts Low/Medium/High
  lifecycle risk text.
- **SX-2 / #469** Project Sourcing now requests the workspace currency for BOM
  coverage and exposes converted BOM offer display prices with top-level FX status.
- **TPS-5 / #452** Project Sourcing BOM rows and the Sourcing Risk report now
  flag TrustedParts lifecycle-risk text, supply-chain-risk text, tariff
  exposure, and EU RoHS non-compliance from the TPS-4 gap fields.
- **TPS-10 / #457** Workspace sourcing settings can now store an optional
  TrustedParts `LanguageCode` for specification translations.
- **TPS-4 / #451** TrustedParts gap-field parsing now surfaces lifecycle
  risk, supply-chain risk, tariff status, manufacturer id, specifications,
  distributor id, RoHS compliance, availability text, quantity multiple,
  formatted price amount, price text, TP current date, and TP response time
  in sourcing DTOs and route responses.
- **TPS-2 / #449** TrustedParts responses are now validated through the
  generated Inventory API v2 models before app DTO mapping; auth moved to the
  `X-Api-Key` header, deprecated `CompanyId` is no longer sent, and TP
  `ErrorMessage` bodies now surface as upstream errors instead of empty results.
- **FB-007 / #437** Active sourcing lists now backfill saved workspace
  sourcing defaults: `active_distributors` is unioned with preferred
  distributors, and saved country/currency values are appended when missing.
  Project Sourcing also defaults distributor filters to the saved/active
  intersection before falling back to the first active distributor.
- **FB-003c / #412** Sourcing capacity now prices the requested build
  quantity when after-purchase capacity floors to zero, fixing missing
  `est_purchase_cost` values at low build quantities.
- **FB-003d / #413** Project Sourcing BOM rows now render the per-row
  lead time returned by the Source-BOM response.

## 2026-05 — security follow-ups

- **SEC2-013 / #72** Invitation accept flow switched to constant-time
  HMAC comparison.  Previously the accept endpoint queried
  `WHERE token_hash = $digest` — a timing oracle because SQL string
  equality is not constant-time.  Fix: `token_hmac` column added to
  `workspace_invitations` (migration 0021, HMAC-SHA-256 keyed on
  `SESSION_SECRET`).  Accept now looks up by `id` (PK, no timing
  oracle) then calls `hmac.compare_digest(hmac_of_supplied, row.token_hmac)`.
  Token returned by the create endpoint is now a composite
  `"{id}:{plaintext}"` string so the frontend passes the PK opaquely.
  **Operator note:** existing pending invitations are invalidated by
  this migration (plaintexts were never stored, so `token_hmac` cannot
  be backfilled).  Revoke and re-issue any outstanding invitations
  after deploying.

## 2026-05 — teardown follow-ups

- **DB-009 / #100** Corrected the `Revision ID:` docstring header in
  `0001_initial.py` (was `2a3353f8b5fe`) and
  `0005_workspace_invitations.py` (was `24ac5d07a692`) to match the
  canonical `revision = '0001'` / `'0005'` constants Alembic actually
  reads. Comment-only edit; `alembic upgrade head` is unchanged. Now
  `git grep` and `alembic show <id>` agree.

## 2026-05 — security remediation (PRs #1 – #9)

Bulk close-out of the 2026-04-30 review (`review-2026-04-30/`,
22 CRITICAL + 38 HIGH findings). PR numbers and merge order:

### Workspace isolation (Tier A)
- **#1 / #2** Cross-workspace FK leaks closed across attachments,
  projects, custom_fields, tags, stock, builds, and `parts.default_storage_location_id`. New shared `assert_in_workspace` /
  `assert_polymorphic_in_workspace` helper in `app/api/_helpers.py` —
  one canonical replacement for the `db.get(Model, id) + manual
  workspace_id check` pattern. `CustomFieldIn.source` dropped from
  the schema (was client-controllable; would let callers forge
  `source='provider'` rows).

### Hardening batch (Tier B)
- **#3** Workspace cookie hardened (httponly + secure-in-prod +
  samesite=lax). Backend container runs as `appuser` uid 1000
  (gosu + idempotent /data chown on boot). `.dockerignore` at repo
  root and `web/` drops build context from 263 MB to ~10 MB and
  stops shipping `.env` into the daemon. `web/Dockerfile.prod` now
  strips `*.map` from the served image (Vite's "hidden" sourcemaps
  go to Sentry only). `/api/docs` / `/redoc` / `/openapi.json`
  disabled in prod. Sentry `before_send` scrubs request body on
  workspace settings PATCH/switch and strips Cookie /
  Authorization / X-Workspace-Id headers — frontend has the
  matching `beforeSend`. All GitHub Actions SHA-pinned with
  `permissions: contents: read` at workflow scope and
  `environment: production` on the deploy job.

### Tier C / D — concrete CRITs
- **#4** Attachment XSS hardening: MIME allow-list (PNG/JPEG/WebP/PDF;
  SVG explicitly excluded), magic-byte sniff defeats `evil.html as
  image/png`, declared-vs-actual MIME mismatch rejects, filename
  sanitised to `[A-Za-z0-9._-]{1,80}` with extension derived from
  validated MIME, `Content-Disposition: attachment` always forced
  on download. New `MAX_UPLOAD_BYTES` config (default 10 MiB);
  upload reads at most `MAX + 1` and 413s the rest. Legacy
  pre-allow-list attachments fall back to `application/octet-stream`
  on download.
- **#5** `/api/sentry-tunnel` rate-limited (`60/min/IP`) and
  body-capped via streaming read (`SENTRY_TUNNEL_MAX_BYTES` default
  200 KiB) — was an open ingress that anyone could pump bytes
  through. DSN allow-list preserved.
- **#6** `bulk_import_from_scan` per-row savepoints. Each row's
  writes wrap in `with db.begin_nested():`; an exception inside
  rolls back only that row. Outer transaction commits surviving
  savepoints. New `row_failed` outcome in the per-row response.
  Provider-call exceptions now `sentry_sdk.capture_exception` —
  preserves row-resilience, makes ops aware.
- **#7** `default_storage_mandatory` bypass closed — predicate
  short-circuited on `storage is None`; now rejects both
  "wrong storage" and "no storage at all" when the part requires
  it.
- **#8** Build consume aggregates demand per `(part, lot, storage)`
  before the per-line check. Two BOM entries claiming 60 each of
  the same 100-piece reel now fail with `have 100, want 120`
  before any `-60` row is written.

### Tooling additions
- `CLAUDE.md` at repo root pins the project's hard invariants
  (append-only ledger, code-enforced workspace isolation, response
  envelope shape, content-addressed asset URLs, `bag_signature`,
  prod-deploy footguns).
- `.claude/` adds project-scoped Claude Code config: hooks for
  pre-edit alembic-migration guard + post-edit pytest-collect +
  end-of-turn `tsc -b`; subagent contracts for the
  `workspace-isolation-checker` and `alembic-migration-reviewer`
  reviewer flows; `settings.json` wires it all to the lifecycle
  events.

### Test footprint
- 224 backend tests passing (was 151 at the start of the
  remediation). Major additions: workspace-isolation matrix across
  every router, attachment allow-list / size-cap / sanitization,
  Sentry-tunnel rate-limit + body-cap, `default_storage_mandatory`
  bypass regression, build-consume aggregation regression,
  bulk-import savepoint regression, security-hardening cookie +
  Sentry-scrubber pins.

## Beyond Phase 10 — production deployment, observability, scan-to-import

### Production live at `parts.matescb.cz`
- VPS-hosted docker-compose stack behind the host's existing Apache 2.4
  + certbot. New apps follow the same pattern (`deploy/parts.matescb.cz.conf`)
  as siblings on the host. Web container's nginx handles `/api/*` →
  backend internally so Apache only ProxyPasses one host port.
- GitHub Actions CI: `backend-tests` (pytest + postgres service container),
  `web-build` (npm ci + vitest + tsc + vite build), `deploy` (SSH to VPS,
  `git reset --hard` + `docker compose up -d --build`). Auto-deploys every
  push to `main` after green tests.
- Nightly `deploy/backup.sh` — pg_dump + uploads tar to `/srv/backups/`
  with 30-day retention.
- Session cookie marked `Secure` when `APP_ENV=prod`; auth `/login` and
  `/signup` rate-limited via slowapi (10/min/IP and 5/hour/IP). uvicorn
  runs `--workers 1` so the in-process bucket store is global.

### Observability — Sentry on both runtimes
- Backend (`sentry-sdk[fastapi]`) and frontend (`@sentry/react`) wired
  via env-driven DSNs. Frontend init lives in a sidecar `instrument.ts`
  imported first per the official sentry-react-sdk skill; React Router
  v6 hooks-based browser-tracing integration, Session Replay with
  `maskAllText` + `blockAllMedia`.
- Same-origin `/api/sentry-tunnel` forwards envelopes through our backend
  so ad-blockers (uBlock, Brave Shields, Pi-hole) don't drop events with
  `ERR_BLOCKED_BY_CLIENT`. Allow-list pinned to the configured
  `SENTRY_DSN` + `VITE_SENTRY_DSN` so it can't be abused as an open
  forwarder.
- `sentryVitePlugin` uploads hidden source maps at build time + tags
  every release with the deploy's git SHA. Sentry groups issues per
  release and auto-resolves them when the next release deploys.

### Provider expansion — Mouser then DigiKey
- Mouser `ProductAttributes` → `specs[]` + image_url persisted as
  `custom_fields` with `source='provider'`. Description-mining picks
  the parametric values out of the prose (resistance, capacitance,
  package, tolerance, …). `Specs` tab on part detail; provider
  source attribution + manual override + refresh-from-provider
  (non-destructive reconciliation).
- DigiKey Product Information V4 alongside Mouser. 2-legged OAuth
  (`client_id` + `client_secret` — second workspace credential column
  in alembic 0009). ProductDetails first; on 404 falls back to keyword
  search (handles distributor-side MPN normalisation, e.g. Molex
  `98266-0897` indexed as `0982660897`). One-shot 401 retry on token
  rotation. DigiKey's `Parameters[]` reliably populates, so most
  parts come back with a real parametric table.
- Workspace settings page picks the provider + stores its credentials.
  Mouser's "Invalid unique identifier" key-rejection is translated to
  an actionable "Re-paste a valid key in Settings…" message.

### Scan-to-import bulk flow
- New `/parts/scan-import` route: scan a stack of bags, see each
  provider lookup land in real time, then import them in one go.
- MIL-STD-130N / ANSI MH10.8.2 bag-code parser (`web/src/lib/bagCode.ts`)
  extracts MPN (1P), quantity (Q), date code (10D/9D), lot (1T),
  serial (1S), order/PO/invoice references (K/1K/14K/11K),
  manufacturer (1V). Three passes — real separators, inline-DI regex,
  plain-MPN fallback for 1D barcodes. Normalises ZXing's printable
  Control-Pictures block (U+241C–U+241F, U+2404, U+2420) back to ASCII
  control chars before parsing.
- Bag traceability persists: `lot_name` (synthesised "Lot X · DC Y")
  creates a Lot row, order/invoice refs become the `stock_entry`
  comment so a physical bag traces back to its source PO months
  later. Scanned quantity always lands on-hand even without a
  storage location.
- Vitest pins down every parser regression we've shipped a fix for.

### Switchable scanner backend (alembic 0010)
- Workspace setting picks `zxing` (royalty-free default) or `scandit`
  (opt-in, requires a workspace-scoped license key). `Scanner.tsx` is
  a thin lazy-import dispatcher; both backends conform to one props
  contract so call sites don't change.
- ZXing-C++ wasm copied from `node_modules` to `public/zxing/` at
  build time so we serve it from our own origin (not the package's
  default jsDelivr CDN). `Cache-Control: public, immutable` on the
  wasm route.
- Camera picker (multi-camera phones) + zoom slider. Hardware mode
  via `track.applyConstraints({ advanced: [{ zoom }] })` when
  available; digital fallback (centre-cropped frame fed to the decoder
  at native resolution + CSS `transform: scale()` on the preview)
  for cameras that don't expose hardware zoom — PC webcams, Firefox.
  Audible click + haptic vibration on each successful read.
  Permission-denied UX with a "Try again" button instead of cryptic
  "Camera API unavailable".
- `/parts/scan` was a single-MPN-lookup page; consolidated into
  `/parts/scan-import` (its duplicate-detection path subsumes the
  lookup case). Old route redirects.

### Engineering hygiene
- Pydantic v1 `class Config:` blocks → `ConfigDict`. `httpx` promoted
  to a runtime dependency (was dev-only; broke first lookup-mpn after
  the prod image was built without `[dev]`).
- Lazy-routed orders/builds/reports/projects/settings — main bundle
  drops from 487 KB → 423 KB. `@sentry/react` carved into its own
  `manualChunks` split so it caches independently.
- vitest + bagCode regression suite added to CI.

## Phase 10 — RBAC + workspace invitations
- Roles enforced (`owner | admin | member | viewer`); `require_role()`
  dependency factory.
- `workspace_invitations` table + token-based accept flow.
- Members & invitations UI in workspace settings; accept-invite UI on
  account page.
- Migration 0005.

## Phase 9 — Serial tracking
- `parts.serialized` boolean (migration 0004).
- `Workspace.serial_tracking_enabled` toggle now enforced on
  `add_stock` and `receive`: qty=1, `serial_number` required.
- Workspace settings UI editable; serial inputs surfaced on
  add-stock and order-receive forms.

## Phase 8 — Meta-parts & sub-assemblies
- CRUD for `PartMetaMember` rows.
- Build engine considers meta-part members the same way it considers
  registered substitutes — meta-part BOM lines now build correctly.
- `Members` tab on the part detail when `part_type='meta'`.

## Phase 7 — BOM import presets
- CRUD on `bom_import_presets`.
- Import wizard now has Save / Load / Manage preset controls.

## Phase 6 — Reports
- `/api/reports/low-stock`, `stock-value`, `bom-shortage`,
  `expiring-lots`.
- `/reports` page with sub-tabs; all CSV-exportable.

## Phase 5 — Builds & consume-from-BOM
- `builds` table (migration 0003).
- `shortage_analysis()` + `consume()` services; ledger rows tagged
  `build_consume` / `build_produce`; output sub-assembly lot when
  the project has `associated_subassembly_part_id`.
- `/builds` UI with auto-fill consumption planner.

## Phase 4 — Purchase orders
- `orders` and `order_entries` tables (migration 0002).
- Line-level receive flow → `source_type='purchase'` lots, ledger
  rows tagged `order_id` / `order_entry_id`.
- `/orders` UI with line editor + receive form.

## Phase 1–3 (initial commit)
- Auth + workspaces (argon2, cookie sessions, multi-tenant).
- Parts (CRUD, archive, scan, substitutes, meta-member table).
- Storage locations.
- Append-only stock ledger; lots with split / parent-lot.
- Projects, BOM CRUD, full CSV import wizard with mappable columns.
- Cross-cutting: attachments, custom fields, tags, global search.
- Migration 0001 (autogenerated; deferred FK on
  `projects.associated_subassembly_part_id` breaks the
  parts↔projects cycle).
