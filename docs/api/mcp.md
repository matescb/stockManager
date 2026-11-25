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

38 tools — 19 read, 19 write. Names and arguments are the agent-facing contract;
the authoritative descriptions are the docstrings in `backend/app/mcp/tools/`,
which are what the model actually reads.

Most `part_id` arguments accept **either** a part id or an exact MPN, because
an assistant reading a schematic has one and an assistant following up on an
earlier result has the other.

### Read

| Tool | Answers |
|---|---|
| `search_parts` | Free-text search over name, MPN, manufacturer, IPN, description |
| `get_part` | One part: specs, catalog metadata, stock by location, CAD status, [lifecycle and compliance](#lifecycle-and-compliance) |
| `get_part_eda` | A part's KiCad configuration, with resolved `PCM_SM_…` refs, its `pin_pad_check`, its `spice_binding` (see [SPICE pin binding](#spice-pin-binding)) and per-file `assets` metadata and review (see [Asset metadata and review](#asset-metadata-and-review)) |
| `find_parts_missing_eda` | "What still needs a footprint?" — by `symbol`/`footprint`/`model3d`/`spice` |
| `stock_levels` | On-hand / reserved / available, one part or the whole inventory |
| `list_storage_locations` | Bins and their constraints |
| `list_categories` | Categories and their slugs |
| `list_projects` | Projects (boards) |
| `get_project_bom` | One project's BOM lines |
| `bom_shortages` | What you are short of to build N boards, with substitute stock split by evaluation state (see [Substitute evaluation](#substitute-evaluation)) |
| `list_substitutes` | A part's substitute links with `relation`, `evaluation`, `evidence`, `evaluated_by` / `evaluated_at` |
| `get_part_policy` | Which fields this workspace requires on a part, and where to find them |
| `validate_part` | Dry-run `create_part`: what the part policy would refuse, nothing written |
| `part_policy_status` | What an existing part still lacks under the part policy |
| `export_parts` | Every part, cursor-paged, with EDA hashes, on-hand / reserved / available, lifecycle and compliance — for audits |
| `check_pin_pad` | Do the symbol's pin numbers and the footprint's pad numbers match? (see [Pin/pad check](#pinpad-check)) |
| `list_pin_pad_issues` | Every part whose pin/pad check fails, warns or cannot run, cursor-paged |
| `list_spice_binding_issues` | Parts whose `sim_pins` (KiCad `Sim.Pins`) is unchecked free text or a binding that no longer holds — cursor-paged, read-only (see [SPICE pin binding](#spice-pin-binding)) |
| `list_part_documents` | A part's local PDFs (datasheet, app notes, PCNs, manuals, uploads) with a Bearer-downloadable `download_url`, plus what is still queued (see [Part documents](#part-documents)) |

### Write

| Tool | Does |
|---|---|
| `sourcing_offers` | Distributor price and stock for a part (see below) |
| `catalog_lookup` | Look an MPN up at DigiKey / Mouser; nothing is written (see below) |
| `catalog_search` | Keyword / parametric search at DigiKey / Mouser with facets; nothing is written (see below) |
| `set_part_eda` | Replace a part's KiCad configuration |
| `upload_eda_asset` | Add a symbol / footprint / 3D model / SPICE file (base64); a SPICE file's `.subckt` / `.model` ports come back as `spice_models` |
| `set_spice_binding` | Map a part's symbol pins to its SPICE model's ports, checked, and derive `sim_pins` (see [SPICE pin binding](#spice-pin-binding)) |
| `set_asset_metadata` | Record a CAD file's origin, SPICE `model_kind`, simulators and validity; never its review (see [Asset metadata and review](#asset-metadata-and-review)) |
| `set_substitute_evaluation` | Record a substitute link's relation, evaluation verdict and evidence (see [Substitute evaluation](#substitute-evaluation)) |
| `import_vendor_zip` | Import a SnapEDA / SamacSys / UltraLibrarian archive (base64) |
| `fetch_lcsc` | Fetch and convert CAD data from LCSC / EasyEDA — **disabled by default**, see [Disabling](#disabling) |
| `add_stock` | Add stock |
| `consume_stock` | Consume stock |
| `move_stock` | Move stock between locations |
| `create_category` | Create a part category |
| `create_part` | Create a part, or report the one that already holds the MPN |
| `set_part_category` | File a part under a category |
| `set_part_specs` | Write a part's own specifications |
| `sync_project_bom` | Update a project's BOM in place from a KiCad export (`dry_run` first) |
| `request_part_document` | Queue an https PDF for the hourly download onto a part — never fetched in the call (see [Part documents](#part-documents)) |

### Catalogue lookup

`catalog_lookup(mpn, manufacturer?, provider?)` asks the workspace's
configured distributor providers — `"digikey"`, `"mouser"`, or `"all"`
(default: the primary plus every secondary with credentials) — about one
manufacturer part number. It writes nothing: no part, no spec rows, no
asset download. It is declared a write because a cache miss spends the
owner's daily distributor allowance ([below](#why-a-price-lookup-counts-as-a-write)).

```json
{
  "mpn": "OPA192IDBVT",
  "match": "exact",
  "records": [
    {
      "source": "digikey",
      "match": "exact",
      "product_url": "https://www.digikey.cz/en/products/detail/…",
      "retrieved_at": "2026-11-24T10:02:11+00:00",
      "mpn": "OPA192IDBVT",
      "manufacturer": "Texas Instruments",
      "description": "IC OPAMP GP 1 CIRCUIT SOT23-5",
      "category": "Instrumentation, Op Amps, Buffer Amps",
      "lifecycle": {"status": "active", "raw": "Active", "discontinued": false,
                    "end_of_life": false, "source": "digikey",
                    "refreshed_at": "2026-11-24T10:02:11+00:00"},
      "compliance": {"rohs": "ROHS3 Compliant", "reach": "REACH Unaffected",
                     "eccn": "EAR99", "hts": "8542.33.0001", "msl": "1 (Unlimited)",
                     "source": "digikey", "refreshed_at": "2026-11-24T10:02:11+00:00"},
      "datasheet_url": "https://www.ti.com/lit/ds/symlink/opa192.pdf",
      "stock": 5234,
      "price_breaks": [{"qty": 1, "unit_price": "52.1000 CZK"}],
      "cache": "miss"
    }
  ],
  "cache": "miss",
  "quota": [
    {"provider": "digikey", "spent": 1, "remaining_day": 640,
     "reserve": 200, "source": "header"}
  ]
}
```

- **One record per provider, never merged.** `source`, the provider's own
  `product_url` (unmodified) and `retrieved_at` are on every record —
  DigiKey's terms require its data to be attributed and not aggregated with
  other data "without distinction". `lifecycle` and `compliance` have the
  [`get_part` shape](#lifecycle-and-compliance), built from this provider's
  record (`source` is the provider, `refreshed_at` the retrieval time;
  Mouser records may add `suggested_replacement`, flagged parts `ncnr`).
- **`match`** is `exact` only when the provider's MPN is the one asked for
  (case-insensitive). DigiKey's keyword fallback and Mouser's partial match
  return `fuzzy` with the MPN actually found in `mpn`. The top-level `match`
  is `exact` if any record is, `none` if there are no records. `found` on
  the underlying `MpnLookupResult` keeps its old meaning; `match` is a new
  key next to it.
- **`candidates`** lists every manufacturer when Mouser has several for one
  exact MPN. With `manufacturer` given, each record carries
  `manufacturer_matches` and the matching candidate becomes the record.
- **`misses`** lists providers that answered "no match"; **`errors`** lists
  providers that refused, each `{provider, code, message}` (plus
  `retry_after_ms` for `provider.pacing`). The call as a
  whole is refused only when no provider answered at all, with the first
  provider's code and the quota of each in the message.
- **`cache: "hit"`** means every answer came from THIS workspace's own
  earlier `catalog_lookup` (24 h for hits, 5 min for clean misses) and spent
  nothing; each record also carries its own `cache`. The agent path never
  answers from the cross-workspace provider cache the UI uses: a hit there
  (or its `retrieved_at`, or `spent: 0`) would reveal that another workspace
  looked the MPN up. A first lookup in a workspace always goes to the
  provider.
- **No transport retries.** The shared provider transport normally retries
  429/503 with backoff (up to 8 s, three times, ADR-0023). Agent calls make
  one attempt; a 429 comes back under `errors` as `rate_limited`, with
  `retry_after_ms` when DigiKey sent `Retry-After`.

### Lifecycle and compliance

`get_part` and each `export_parts` item carry two blocks (SM-004), read from
the catalog rows the provider refresh already stores — no second copy, no
write:

```json
"lifecycle": {"status": "active", "raw": "Active", "discontinued": false,
              "end_of_life": false, "source": "digikey",
              "refreshed_at": "2026-11-20T08:14:03+00:00",
              "alternates": [{"source": "mouser", "status": "obsolete", "raw": "Obsolete",
                              "discontinued": true, "end_of_life": false,
                              "refreshed_at": "2026-11-21T09:00:00+00:00"}]},
"compliance": {"rohs": "ROHS3 Compliant", "reach": "REACH Unaffected",
               "eccn": "unknown", "hts": "unknown", "msl": "unknown",
               "source": "digikey", "refreshed_at": "2026-11-20T08:14:03+00:00"}
```

- **`source`** is the provider that supplied the values, or `manual` for a
  value a user entered or edited. Sources rank manual, then DigiKey, then
  Mouser (`PROVIDER_PRECEDENCE`); every other source that answered is listed
  under `alternates`, so a part obsolete at one distributor and active at the
  other shows both.
- **`unknown`** means no source supplied the value (and `source: "unknown"`
  when none supplied any). A provider's missing `Discontinued` / `End of
  life` flag reads `false`; a manual source asserts no flags (`unknown`).
- **`reason`** says why, whenever `lifecycle.status` is `unknown` (and on
  `compliance` when no source supplied any of its fields). It never turns
  `unknown` into a guess — read it, don't infer `active` from it:

  | `reason` | When | Also |
  |---|---|---|
  | `never_fetched` | No provider link: no provider was ever asked about this part | `source: "unknown"`, `refreshed_at: null` |
  | `provider_silent` | A provider is linked and was refreshed, but sent no value | `source` / `refreshed_at` name that provider and its last refresh (`part_provider_links.last_refresh_at`); `note` |
  | `unrecognised` | The provider sent a status string the table below does not map | `raw` keeps it |

  **Mouser silence is normal.** Mouser fills `LifecycleStatus` only for parts
  outside normal production (new product, NRND, end of life, obsolete) and
  leaves it empty otherwise; the adapter stores nothing for an empty value.
  A Mouser-only part in plain production therefore reads:

  ```json
  "lifecycle": {"status": "unknown", "discontinued": "unknown",
                "end_of_life": "unknown", "source": "mouser",
                "refreshed_at": "2026-11-21T09:00:00+00:00",
                "reason": "provider_silent",
                "note": "Mouser states lifecycle only for parts that are not in normal production"}
  ```

  The status stays `unknown` on purpose: an empty field is also all an agent
  would see for a part Mouser has no lifecycle data on. Link DigiKey as well
  (DigiKey states a `ProductStatus` on its products) when the status matters.
  `catalog_lookup` uses the same reasons for its live record, where the
  record itself is the fetch (`provider_silent` or `unrecognised`).
- **`refreshed_at`** is the later of the rows' `updated_at` and the
  provider link's `last_refresh_at`.
- **ECCN, HTS and MSL** are on ADR-0034's junk list: the refresh retires
  them instead of storing them, so on a stored part they are `unknown`
  unless entered by hand. `catalog_lookup` returns them from the live record.
- **`status`** is normalised; `raw` keeps the provider's string. Without a
  status string a `Discontinued` flag gives `discontinued`, an `End of
  life` flag `last-time-buy`.

| raw (DigiKey `ProductStatus` / Mouser `LifecycleStatus`) | `status` |
|---|---|
| Not Recommended for New Designs, Not For New Designs, NRND | `nrnd` |
| Last Time Buy, End of Life | `last-time-buy` |
| Obsolete | `obsolete` |
| Discontinued at Digi-Key, Discontinued | `discontinued` |
| Preliminary, Pre-Release, Coming Soon | `preliminary` |
| Active, Production, New Product, New at Mouser | `active` |
| anything else (e.g. Factory Special Order), or nothing | `unknown` |

### Catalogue search

`catalog_search(keywords, category_id?, manufacturer_ids?, parameter_filters?,
status?, in_stock?, rohs?, has_datasheet?, sort?, limit?, offset?, provider?,
list_categories?)` is part research: "0603 C0G 100 nF, at least 50 V, active,
in stock". It writes nothing and follows `catalog_lookup`'s rules — declared a
write, one candidate list per provider (never merged), `source` /
`product_url` / `retrieved_at` on every candidate, the [provider
quota](#provider-quota) with `quota` per provider, one attempt with no
transport retries.

```json
{
  "keywords": "100nF 0603 C0G", "limit": 2, "offset": 0,
  "total": {"digikey": 1342},
  "candidates": [
    {"source": "digikey", "mpn": "GRM1885C1H104JA01D",
     "manufacturer": "Murata Electronics",
     "description": "CAP CER 0.1UF 50V C0G/NP0 0603", "category": "Ceramic Capacitors",
     "status": "Active",
     "key_parameters": {"Capacitance": "0.1 µF", "Voltage - Rated": "50V"},
     "stock": 41230, "unit_price_qty1": "9.4100 CZK",
     "datasheet_url": "…", "product_url": "https://www.digikey.cz/…",
     "retrieved_at": "2026-11-24T10:02:11+00:00"}
  ],
  "facets": {
    "source": "digikey",
    "categories": [{"id": "60", "name": "Ceramic Capacitors", "count": 2, "root": "Capacitors"}],
    "categories_from": "candidates",
    "manufacturers": [{"id": "490", "name": "Murata Electronics", "count": 312}],
    "status": [],
    "parametric_filters": []
  },
  "hints": ["DigiKey returns parametric_filters and status only once a search is narrow …"],
  "applied_filters": {"digikey": {"Keywords": "100nF 0603 C0G", "Limit": 2, "Offset": 0,
                                  "SortOptions": {"Field": "QuantityAvailable",
                                                  "SortOrder": "Descending"}}},
  "applied_sort": {"digikey": {"sort": "-stock", "field": "QuantityAvailable",
                               "order": "Descending", "source": "default"}},
  "cache": "miss",
  "quota": [{"provider": "digikey", "spent": 1, "remaining_day": 640,
             "reserve": 200, "source": "header"}]
}
```

- **Narrow with ids from `facets`.** `category_id`, `manufacturer_ids`,
  `status` and `parameter_filters: [{parameter_id, value_ids}]` take the ids
  DigiKey returned in a previous answer. Parametric filters need
  `category_id` (DigiKey scopes parameter ids per category), else
  `provider.invalid_filter`. `applied_filters` echoes the request body that
  was actually sent, per provider.
- **Facets are sparse on a broad search, and that is DigiKey.** In live
  KeywordResponses `Manufacturers` is always filled, but `TopCategories`,
  `Status` and `ParametricFilters` are `null` unless the result set is
  narrow (in practice one leaf category). So narrow in two steps: take a
  leaf `category_id` from `facets.categories`, re-query, and take
  `parameter_filters` ids from that answer. When DigiKey sends no
  `TopCategories`, `facets.categories` is built from the page's candidates
  (`categories_from: "candidates"`, `count` = candidates on the page). A
  `ParameterFilterRequest` on a parent category is a DigiKey 400.
- **Values go in `parameter_filters`, not `keywords`.** DigiKey's keyword
  matcher sees values as its listings write them (`0.1µF`, `100pF`), so
  `"100nF 0603 C0G 50V"` returns 0 while `"ceramic capacitor 0603 C0G"`
  returns thousands. We do not rewrite the query. A parametric
  `UnitOfMeasure` value id is its text (`"0.1 µF"`).
- **DigiKey's own 4xx text.** A DigiKey 4xx other than 401 / 403 / 429
  carries `provider_message`: the message fields of its `DKProblemDetails`
  body (`detail`, `errors`; `title` only as a fallback), with control
  characters dropped, URLs redacted and the text capped at 300 characters.
  It never includes `type`, `instance`, the request URL or headers. It
  appears on that provider's `errors` entry, or in the refusal text when no
  provider answered. "Parametric Filtering Not Support at Parent Category
  Level" also adds a `hint`: use a leaf `category_id`, or fewer keywords.
- **`hints`** (when present) gives the agent the next step: narrow by
  category to get parametric filters, drop an SI-prefixed value
  (`100nF`, `4.7uH`) from keywords after a zero-result search, or — when
  keywords hold a package / size token (`0603`, `SOT-23`, `SOIC-8`, `QFN`…)
  and no parametric filter is applied — narrow the package through the
  "Package / Case" parametric facet: DigiKey's keyword matcher does not
  restrict it (`0603` returns 0201 parts first).
- **Default order: most in stock first.** DigiKey's own default order is not
  relevance (the same first five come back across differently filtered
  queries), so a call without `sort` sends `SortOptions {Field:
  "QuantityAvailable", SortOrder: "Descending"}` (`-stock`). An explicit
  `sort` is sent unchanged; its names map to the v4 `SortOptions.Field`
  enum (`price` Price, `stock` QuantityAvailable, `mpn`
  ManufacturerProductNumber, `manufacturer` Manufacturer, `status`
  ProductStatus, `min_qty` MinimumQuantity; `-` = Descending).
  `applied_sort` reports per provider `{sort, field, order, source:
  "caller" | "default"}`; Mouser takes no sort and reports `{sort: null,
  source: "provider"}`.
- **Mouser does keyword search only** — `keywords`, `in_stock`, `rohs`
  (mapped to `searchOptions` None / Rohs / InStock / RohsAndInStock), `limit`,
  `offset`. Any DigiKey-only argument (`category_id`, `manufacturer_ids`,
  `parameter_filters`, `status`, `has_datasheet`, `sort`, `list_categories`)
  with `provider: "mouser"` is refused with `provider.unsupported_filter`;
  with `"all"`, Mouser is listed under `errors` with that code and DigiKey
  still answers. Nothing is silently dropped.
- **Paging.** `limit` is per provider, default 20, clamped to 1–50; page with
  `offset += limit`. `total` is per provider.
- **Compact.** At most 8 `key_parameters` per candidate; facets are capped
  (10 categories, 20 manufacturers, 20 parametric filters of at most 12
  values each, `values_truncated: true` when cut).
- **`list_categories: true`** adds DigiKey's category tree two levels deep as
  `categories`; `keywords` may then be empty. The tree is cached in-process
  for a day.
- **Nothing is persisted.** No part, spec, attachment, cache or audit row.
  DigiKey's terms forbid building "your own database" from its data, so the
  most kept is an in-process copy of a DigiKey response for 10 minutes, keyed
  by workspace and the exact request (`cache: "hit"`, `spent: 0`). Mouser's
  caching terms have not been read, so a Mouser answer lives for the request
  only.

### Provider quota

DigiKey publishes 1000 Product Information calls a day and suspends an
application for ten minutes ("403 Forbidden") when a per-second threshold is
exceeded; Mouser's limits are reported as 1000 a day but unverified. That
allowance is shared by the UI lookup, scan import, refresh-from-provider and
the `provider-refresh` job, so the agent path is held to rules the others
are not (`backend/app/domain/parts/services/provider_quota.py`):

| Rule | Default | Setting |
|---|---|---|
| Live calls to one provider from one workspace are at least this far apart; a call sooner than that is refused with `provider.pacing` and `retry_after_ms` (the server never sleeps on it) | 1 s | `PROVIDER_AGENT_MIN_SPACING_SECONDS` |
| Agent tools refuse when fewer calls than this remain today | 200 | `PROVIDER_AGENT_QUOTA_RESERVE` |
| Daily allowance assumed when the provider sent no header today | 1000 / 1000 | `PROVIDER_DAILY_LIMIT_DIGIKEY` / `_MOUSER` |

Every answer carries `quota`, one entry per provider asked: `spent` (calls
this answer cost — 0 on a cache hit), `remaining_day`, `reserve`, and
`source`. `source: "header"` is DigiKey's own `X-RateLimit-Remaining` from
today, less any calls this process made since; `"local-count"` is the
configured allowance minus the calls this process counted, which cannot see
the `provider-refresh` job's separate container. The state is per process,
like the provider cache (one uvicorn worker in prod). Both numbers are
approximate: `spent` is the change in this process's count for that
credential during the call, so a UI lookup running at the same moment can
land in it, and a DigiKey 401 token refresh counts a second call.

A DigiKey 403 that looks like the burst suspension (a `Retry-After` header,
or an error body mentioning throttling, rate limits or suspension) opens the
circuit breaker **for that credential** for ten minutes. Every lookup with
the same DigiKey client id in that window — agent, UI or refresh — fails
fast with "suspended … retry in Ns" instead of reaching DigiKey; cache hits
are still served, and other workspaces with their own credentials are
unaffected (breakers are keyed by provider and a hash of the credential).
It is never reported as "no match", and the `provider-refresh` job halts on
it (exit 3) the way it does on a 429. Any other 403 — an app not subscribed
to Product Information, a revoked client — is reported as a credential error
(`provider.upstream_error`) and counts toward the ordinary breaker (five
hard failures open it for 60 s, again per credential).

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

### Part policy

The workspace's part policy ([part-policy](part-policy.md)) says which fields
a part must have. `create_part` enforces its `when="create"` rules with the same
evaluator as `POST /api/parts`, but a refusal is a **result, not a tool
error** — the agent has to act on the list field by field, and one line of
error text cannot carry it:

```json
{ "found_existing": false, "created": false, "error": "part.policy_violation",
  "message": "part is missing 1 field(s) required by the workspace part policy",
  "missing": [ { "field": "manufacturer", "rule_id": "mfr", "when": "create",
                 "message": "Manufacturer is required", "hint": "as printed on the datasheet" } ] }
```

Nothing is written. Read `created` alongside `found_existing`: a success is
`created: true` plus `part` and `incomplete` — the `complete` rules still unmet,
the same items `part_policy_status` returns. `validate_part` takes
`create_part`'s arguments and returns `{ok, missing, existing_part}` exactly as
`POST /api/parts/validate` does. The loop: `get_part_policy` once,
`validate_part` until `ok`, `create_part`, then attach and check
`part_policy_status` until `complete`.

### Specs

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
5. `check_pin_pad(part_id_or_mpn=…)` — confirm the symbol's pin numbers and the
   footprint's pad numbers match before the part goes on a board. Vendor
   symbols and footprints come from different generators.
6. For a part with a SPICE model: `set_spice_binding(part_id_or_mpn=…,
   pin_map={…})` — **after** step 4, because `set_part_eda` resets `sim_pins`
   (see [SPICE pin binding](#spice-pin-binding)).

### SPICE pin binding

`part_eda.sim_pins` is what the KiCad HTTP library emits as `Sim.Pins`
(`"1=OUT 2=V- 3=IN+ 4=IN- 5=V+"` — KiCad's `symbolPin=modelPin` form, space
separated). It used to be free text nothing checked; a wrong order simulates
silently wrong (SM-009). The emitted field names `Sim.Device`, `Sim.Pins`,
`Sim.Params` and `Sim.Library` are unchanged.

1. `upload_eda_asset(kind="spice", …, part_id=…)` parses the file and returns
   `spice_models: [{name, kind: "subckt"|"model", ports, device_type?}]`
   (plus `ng010_header` for a file exported by the ngSPICE model library's
   `bin/export-model`). When the part now points at that file and it defines
   exactly one model, the subckt and ports are stored as an **unvalidated**
   binding (`spice_binding_updated: true`). New bytes on a part that already
   had a binding mark it unvalidated and keep its `pin_map` for
   re-confirmation; nothing is deleted.
2. `set_spice_binding(part_id_or_mpn, pin_map, subckt?, symbol_pins?,
   unconnected_ports?)` checks `pin_map` — `{symbol_pin: port | null}`, null
   meaning not connected — against the symbol (a hosted symbol's pins are
   read from its file; otherwise, and for a derived `extends` symbol, pass
   `symbol_pins`) and the model: every symbol pin mapped, every target port
   present, every port used exactly once or listed in `unconnected_ports`
   (an LTspice `NC` node, the other channels of a one-channel model). Two
   symbol pins on one net (a second VIN pin, an exposed pad) map one to the
   port and the rest to null. It then stores the binding as validated and
   writes `sim_pins` in symbol-pin order. It touches no other column of the
   part's configuration.
   - `model_kind="native_primitive"` records a part simulated as KiCad's
     built-in `R`, `C`, `L` (ports `+ -`) or `D` (`A K`) with `params`
     (`Sim.Params` text) and no file; `pin_map` is optional (without it the
     record is kept, `not_confirmed`).
   - `model_kind="none"` records that there is no model, with `fallback`
     text (what to simulate with instead). It takes no `pin_map`.
   - From the ngSPICE inventory manifest (`ngspice-inventory-manifest/1`),
     pass a part's `native` object as `native` (its `letter` stands in for
     `device_type`, an R/C/L value for `params`, e.g. `c=1e-10`;
     `loses_behaviour` comes back as a warning) and its `fallback` as
     `fallback_token` (`vendor_model_wanted`, `native`, `attended_ltspice`,
     `external_model:<name>`; anything else is
     `eda.spice_fallback_token_unknown`) plus `fallback` (the detail).
   - A library model that exists but has **no loadable export** (the
     manifest's `export_reason`, "does not load on any build: NG-012 …") is
     `model_kind="none"` with `subckt` naming the model, `export_reason`, and
     `fallback_token="attended_ltspice"`. A part the manifest gives a native
     letter but no held value is `none` with `fallback_token="native"`,
     `native` (stored only) and `native_reason`.
   - Both file-less kinds take an optional `source` (e.g. the ngSPICE
     inventory manifest), refuse a part with a SPICE file attached
     (`eda.spice_kind_conflict`) and never write `part_eda` — the HTTP
     library emits `Sim.*` only for a part with a file. A later upload of a
     file onto the part turns the record into an unconfirmed file binding.
3. `get_part_eda` returns `spice_binding`: `{source: "binding", validated,
   issues, subckt, model_kind, device_type, ports, pin_map, sim_pins,
   model_sha256, model_source, build_requirement, grade, header_pins, …}`, or
   `{source: "free-text", validated: false, sim_pins}` for a hand-written
   value with no binding. The key is **always present**, with a `state`:
   `bound`, `native_primitive` or `none` for a stored binding; `free_text`;
   `unbound` — `{source: "none", state: "unbound", validated: false,
   spice_datafile_id, spice_name}`, a SPICE file attached and nothing bound;
   or `no_model` — `{source: "none", state: "no_model", validated: false}`.
   A missing key means a server without SM-009. `validated` is computed at read time — a different
   file, different bytes or an edited `sim_pins` make it false, whichever
   path made the change. `issues` says why: `not_confirmed`,
   `awaiting_reconfirmation`, `model_changed`, `model_detached`,
   `sim_pins_edited`, `polarity_needs_person`; for the file-less kinds `no_model` (always, for
   `none`) and `model_file_attached`.
4. `list_spice_binding_issues(cursor?, page_size≤100)` lists every part
   that is not validated — including a part with a SPICE file attached and
   nothing bound (`source: "none"`, issue `unbound`, with the file's
   `spice_name`, `models` and, for a one-model file, `subckt` and `ports`) —
   with, for free text, what comparing it with the attached file found (`port_count_mismatch`, `port_unknown`,
   `sim_pins_unparseable`, `model_ambiguous`, `no_model_in_file`,
   `model_unreadable`, `no_model_attached`). File-less records are listed
   only when a file has since been attached. It reads only.

**ngSPICE export headers.** A file exported by the ngSPICE model library
carries a `* Key: value` header. Legacy NG-010 headers (no `Export-Schema:`)
are read as before. `ngspice-export/1` adds `Kind:`, `Build-Token:`,
`Valid-For-Basis:`, one `Port:` line per port (function, whether it is on the
LTspice symbol, whether it is internally unconnected), `Channels:` and
`Match:`. The binding stores them and `spice_binding` reports them as
`export_schema`, `export_kind`, `build`, `valid_for_basis`, `port_functions`,
`ports_on_symbol`, `suggested_unconnected_ports`, `channels`, `match` and
`corrections`. Three effects on `set_spice_binding`: an omitted
`unconnected_ports` defaults to the ports the header marks internally
unconnected (pass `[]` to override; a warning says so); `pin_map` may name a
port by its function (`"OUT"` for port `11`) when that function names exactly
one port; a model covering fewer channels than the package warns
`partial_channels`; and when the header's `Polarity:` line says a person must
name the + terminal — the part is polarised but the model is symmetric R/L/C,
or polarity is not established — the binding carries issue
`polarity_needs_person` (so `validated: false`, and
`list_spice_binding_issues` lists it) until `positive_pin` names the symbol
pin that is the part's + terminal. `spice_binding` reports `polarity`
{`ports`, `model_symmetric`, `basis`, `part_polarized`, `needs_person`} and
`positive_pin`. Any other `Export-Schema` major version is refused —
at upload, before anything is stored, and at binding — with
`eda.spice_export_schema_unsupported`.

**What the parser reads.** Top-level `.subckt NAME ports… [PARAMS: …]` with
`+` continuations, `*` / `;` / `$` comments, any case; nested subcircuits are
skipped. The port list ends at `PARAMS:` or the first `k=v` token. A
`.model` card gets the pin names KiCad's own models use: `D` → `A K`;
`NPN`/`PNP` → `C B E`; `NJF`/`PJF`/`NMOS`/`PMOS`/`VDMOS` → `D G S`. Any
other card type is refused as `eda.spice_model_not_checkable`. A file with
several models needs `subckt`.

### Asset metadata and review

SM-010. Each hosted CAD file in a part's slots — `symbol`, `footprint`,
`model3d` (the footprint's first 3D model by position, the one KiCad places)
and `spice` — carries metadata for **its current bytes**. `get_part_eda`
returns it under `assets`:

```json
"assets": {
  "spice": {
    "id": "…", "name": "OPA192.lib", "sha256": "…", "source": "manual",
    "origin": "https://…/sbom123.zip", "origin_from": "metadata",
    "model_kind": "vendor", "simulators": ["ngspice-43"],
    "valid_for": ["dc", "ac", "transient"], "not_valid_for": ["noise"],
    "note": null,
    "review": {"state": "unreviewed", "by": null, "by_name": null,
               "at": null, "against": null}
  }
}
```

- A slot that is empty or names a local KiCad library (`*_ref_external`) has
  no entry. `source` is the existing server-set import channel, unchanged.
- The metadata is keyed by the file row **and its sha256**. New bytes — a
  re-emitted rename, or a different file in the slot — read as `unreviewed`
  with no metadata; the old version's record is kept.
- `origin_from: "spice_header"` means no `origin` was recorded and the value
  is the NG-010 header's `model_source` from the [SPICE pin
  binding](#spice-pin-binding) for the same bytes.

`set_asset_metadata(part_id_or_mpn, kind, origin?, model_kind?, simulators?,
valid_for?, not_valid_for?, note?)` merges: only the arguments passed change,
and `""` / `[]` clears one. `model_kind` (`vendor` | `converted` | `fitted`)
applies to `spice` only. Lists hold up to 20 entries and are de-duplicated.
Member tokens may call it; a read-only token gets `auth.token_read_only`.

**Review is not writable from any token.** `review_state` /
`review_against` are accepted only to refuse them with
`eda.review_requires_owner`, writing nothing. A workspace owner or admin
records a review in the app (CAD tab → File review), which calls
`PUT /api/parts/{id}/eda/{kind}/review` from a browser session — see
[eda.md](./eda.md#put-apipartspart_idedakindreview).

### Substitute evaluation

A substitute link (SM-012) carries `relation` (`pin-compatible` |
`footprint-compatible` | `functional`, absent when not stated), `evaluation`
(`not-evaluated` | `evaluated-ok` | `rejected`; every link that predates
migration 0089 reads `not-evaluated`), `evidence` (a free reference such as a
simulation id, `SIM-012`, max 500 characters), and `evaluated_by` /
`evaluated_at` — who last changed any of the three, and when.

`list_substitutes(part_id_or_mpn)` lists every link usable in place of the
part — its own links plus bidirectional links registered from the other side,
the same set `bom_shortages` counts. `set_substitute_evaluation(part_id_or_mpn,
substitute_id_or_mpn, relation?, evaluation?, evidence?)` merges: only the
arguments passed change, `""` clears `relation` or `evidence`. It never
creates or removes a link (that is done in the app). Member tokens may call
it; a read-only token gets `auth.token_read_only`.

`bom_shortages` keeps its existing numbers: `short_by` and
`substitute_available` count every registered substitute whatever its
evaluation, exactly as before. Added alongside, per row:

| Key | Meaning |
|---|---|
| `substitute_available_by_evaluation` | `{evaluated_ok, not_evaluated, rejected}` — `substitute_available` split by the link's state; the three sum to it. Meta-part members count as `evaluated_ok` (membership is the owner's statement that they are the part) |
| `short_by_evaluated_only` | `max(0, required − (available + evaluated_ok))` |

and a top-level `covered_by_unevaluated_substitutes` list (same row shape):
lines with `short_by` 0 but `short_by_evaluated_only` > 0 — covered only
because a not-evaluated or rejected substitute is counted. Treat them as
short until the substitute is evaluated. The REST shortage rows
(`GET /api/builds/{id}`, stage shortage, `GET /api/reports/bom-shortage`)
carry the same two keys.


1. `list_projects()` — projects, newest first, with their ids.
2. `bom_shortages(project_id=…, build_qty=N)`. One row per line that cannot be
   covered, with `required`, `available` and `short_by`. Substitutes and
   meta-part members count towards availability; do-not-populate lines and
   lines with no linked part are skipped. An empty `shortages` list means the
   build is covered, and `lines_checked` says how many lines that verdict
   rests on. Read `covered_by_unevaluated_substitutes` too: those lines are
   covered only by substitutes nobody has evaluated (see [Substitute
   evaluation](#substitute-evaluation)).
3. `get_project_bom(project_id=…)` only if you need the lines themselves —
   `designators`, per-board `quantity`, `dnp`. The shortage answer does not
   require it.
4. Per shorted part: `stock_levels(part_id=…)` for the `on_hand` / `reserved`
   / `available` split, and `sourcing_offers(part_id=…, qty=…)` for price and
   distributor availability. `sourcing_offers` spends metered quota and is
   rate-limited to 60/minute — call it for the shortages, not for the BOM.

### Audit the whole inventory

`search_parts`, `find_parts_missing_eda` and `stock_levels` are capped, and no
query is guaranteed to split the inventory into pages under the cap. For an
audit, page `export_parts` instead:

1. `export_parts(page_size=200)` — no `cursor` on the first call.
2. Call again with `cursor=<next_cursor>` until `next_cursor` is `null`.

Pages are ordered by part id (keyset paging), so every part present for the
whole walk is returned exactly once; a part created mid-walk may be missed but
nothing is ever repeated. Archived parts are skipped unless
`include_archived=true` (they then carry `archived: true`). Each item:

```json
{"id": "…", "name": "…", "mpn": "…", "manufacturer": "…",
 "category": {"id": "…", "name": "Ceramic", "path": "Capacitors / Ceramic"},
 "eda": {"symbol": {"present": true, "sha256": "…"},
         "footprint": {"present": true, "ref_external": "Resistor_SMD:R_0603_1608Metric"},
         "model3d": {"present": false}, "spice": {"present": false}},
 "stock": {"on_hand": 40, "reserved": 12, "available": 28},
 "part_url": "…"}
```

`sha256` is the hosted file's content hash (for `model3d`, the footprint's
first model by position); a slot that points into the user's own local KiCad
library is present with `ref_external` and no hash. `present` is computed by
the same helper as `find_parts_missing_eda`, so the two never disagree.
`page_size` above 200 is **clamped** to 200 (not refused); the result's
`page_size` echoes the value used. Each page costs a fixed handful of queries
regardless of size. Works with a read-only token.

### Pin/pad check

`check_pin_pad(part_id_or_mpn)` compares the part's symbol pin numbers with its
footprint's pad numbers. It is computed on every call from the files the part
uses now, so it is never stale; it carries the two `sha256`s it was computed
on. `get_part_eda` returns the same object under `pin_pad_check`.

```json
{"part_id": "…", "part_url": "…", "verdict": "pass_with_warnings",
 "symbol_sha256": "…", "footprint_sha256": "…",
 "symbol_ref": "PCM_SM_ics:FT232RQ", "footprint_ref": "PCM_SM_ics:QFN-32_EP",
 "findings": {
   "classified": [
     {"kind": "pad_without_pin", "number": "5", "class": "nc_pin",
      "reason": "signal-sized pad inside the symbol's pin numbering; likely an NC package pin"},
     {"kind": "pad_without_pin", "number": "33", "class": "exposed_pad",
      "reason": "pad is 41x the median pad and in the body's centre",
      "accepted": {"by": "…", "by_name": "Ann", "at": "2026-11-25T09:00:00+00:00",
                   "note": "EP to GND pour"}}],
   "pins_without_pad": [], "pads_without_pin": ["5", "33"],
   "duplicate_pin_numbers": [], "no_connect_pins_without_pad": [],
   "mounting_pads_without_pin": []},
 "electrical_count": 0, "warnings_count": 1, "accepted_count": 1,
 "stale_acceptances": [],
 "checked_at": "2026-11-25T12:00:00+00:00"}
```

**Verdicts.** `fail` — an `electrical` finding stands. `pass_with_warnings` —
only non-electrical findings stand: look at them, they do not fail.
`pass` — nothing stands. `not-checkable` (with `reason` and `detail`) —
**`not-checkable` is NOT a pass**: nothing about the part was compared.

**Findings.** `findings.classified` has one `{kind, number, class, reason}` per
finding. `kind` is `pad_without_pin`, `pin_without_pad` or `duplicate_pin`.
`class` is `electrical`, `exposed_pad`, `nc_pin`, `shell` or `mechanical`; only
a pad without a pin can be anything but `electrical`. The older lists
(`pins_without_pad`, `pads_without_pin`, `duplicate_pin_numbers`,
`no_connect_pins_without_pad`, `mounting_pads_without_pin`) are kept unchanged
for existing callers and carry no class — read `classified`.

**Classes** (`backend/app/domain/eda/pin_pad_classify.py`; geometry and pad type
first, names second; the first rule that matches wins, and when unsure the
answer is `electrical`):

- `mechanical` — the pad is numbered `~` (KiCad's "unnamed") or named `MP` /
  `MH` (optional number) or exactly `M`.
- `shell` — named `SH` (optional number), `SHIELD` or `SHELL`; or the part is a
  **connector** (its category's name, library slug or `J` reference prefix, or
  the footprint's name / descr / tags, say so) and the pad sits off the
  matched pads' pin row, or is ≥ 2× the median pad and touches the courtyard
  edge. Outside a connector, geometry never makes a shell (a DPAK's drain tab
  is a real open).
- `exposed_pad` — the pad is ≥ 4× the median pad, in the middle half of the
  courtyard and clear of its outer 15 %; or numbered above the symbol's highest
  pin, ≥ 2× the median and placed the same way; or every pad of the number lies
  inside such a pad (thermal vias numbered on their own).
- `nc_pin` — a numeric, signal-sized (≤ 2× median) pad whose number lies within
  the symbol's own numbering (≤ its highest pin and ≤ the footprint's count of
  signal pads): a gap such as pad 4 of a SOT-23-5 whose symbol has 1, 2, 3, 5.
- `electrical` — everything else, and always every pin without a pad and every
  duplicate pin.

The "median pad" is the median area of the pads the symbol does claim.

**Acceptances.** A workspace owner or admin can accept a finding in the app
(CAD tab → Pin/pad check; REST in [EDA § Pin/pad check](eda.md#pinpad-check-and-accepted-findings)).
No MCP tool writes one. An accepted finding shows `accepted: {by, by_name, at,
note}` and no longer counts toward the verdict or the counts
(`electrical_count` / `warnings_count` count standing findings,
`accepted_count` the accepted ones). An acceptance is tied to both file
hashes: once either file changes it appears under `stale_acceptances` and
excuses nothing. `electrical` findings can be accepted only with a note.

The base rules:

- Numbers are compared as trimmed, case-sensitive strings (BGA `A1` is not
  `a1`). Pins with an empty number are ignored.
- Only **electrical** pads count: a non-empty number, type `smd`, `thru_hole` or
  `connect`, and a copper layer. Unnumbered pads, `np_thru_hole` (NPTH /
  mechanical) pads and paste- or mask-only pads are never findings. Several
  pads with one number (an exposed pad and its thermal vias, or an EP numbered
  like a ground pin) are legal.
- A multi-unit symbol is checked as the union of its units' pins. Body style 1
  is checked; a De Morgan style 2 with different pin numbers is
  `not-checkable` (`body_styles_differ`).
- A `no_connect` pin with no pad goes under `no_connect_pins_without_pad` as
  information; it is not a finding.
- A copper pad named like a mounting or shield pad — `MP`, `MH` or `SH` with an
  optional number, exactly `M`, `SHIELD` or `SHELL`, in any case — that no pin
  claims is also listed under `mounting_pads_without_pin`. `S`, `G`, `M1` and
  `GND_TAB` are not mounting names: `G` / `D` / `S` are a FET's real pads. A
  symbol that has an `MP` pin matches the `MP` pad as usual.
- The same pin number twice in one unit is `duplicate_pin`, except stacked pins
  (same position) and duplicate power pins of which at most one is visible.
- `not-checkable` reasons: `symbol_missing`, `footprint_missing`,
  `symbol_external` / `footprint_external` (the entry is in the user's local
  KiCad library, including a category default), `symbol_unreadable` /
  `footprint_unreadable`, `symbol_derived` (the symbol `extends` one that is
  not stored), `body_styles_differ`, `symbol_too_complex` (over 10,000 pins,
  256 units or 4 body styles: the check's work is bounded).

`list_pin_pad_issues(verdict="all"|"fail"|"not-checkable"|"warnings", cursor, page_size)`
walks every active part whose own EDA configuration names a symbol or a
footprint, keyset-paged by part id like `export_parts`, and returns the ones
with the asked verdict (`warnings` = `pass_with_warnings`; `all` = `fail`,
`not-checkable` and `pass_with_warnings`; plain `pass` is never listed). Each call scans at most 500 parts, so a page can be short or
empty while `next_cursor` is still set — continue until it is `null`. Works
with a read-only token.

### Part documents

A part's paperwork lives as local PDFs: the datasheet, and — since SM-016 —
application notes, technical notes, PCB design notes, programming / reference
manuals and PCNs ([ADR-0036](../adr/0036-part-documents-cron-only-any-host.md)).
They arrive three ways: DigiKey's product `MediaLinks` (filtered to those
document types) are queued whenever a part is created from or refreshed against
DigiKey; an agent queues one with `request_part_document`; a user uploads one.
Queuing never downloads anything — the hourly `datasheet-backfill` job does,
under the datasheet policy (HTTPS, pinned public IP, no redirects, 10 MB,
PDF only).

1. `list_part_documents(part_id_or_mpn="STM32G031K8T6")` — optionally
   `kinds=["application_note", "pcn"]`.
2. For each entry in `documents`, GET its `download_url` with the **same**
   header: `Authorization: Bearer <token>`. It is the ordinary attachment
   download route (`GET /api/attachments/{id}/download`), workspace-scoped,
   always `Content-Disposition: attachment`; a GET, so a **read-only** token is
   enough.
3. Anything in `pending` is not downloaded yet: `status` `queued` (never
   tried), `retrying` (failed with `failure_code`, retried after a day) or
   `failed` (gave up — e.g. `http_404`, `unexpected_type` for a page that is
   not a PDF).

```json
{"part_id": "…",
 "documents": [{"id": "…", "kind": "application_note", "title": "AN2867 …",
                "file_name": "STM32G031K8T6-AN2867_….pdf", "size_bytes": 812345,
                "sha256": "…", "mime": "application/pdf",
                "source_url": "https://www.st.com/…/an2867.pdf",
                "retrieved_at": "2026-11-24T10:00:00+00:00",
                "download_url": "https://parts.example.com/api/attachments/…/download"}],
 "pending": [{"source_url": "https://…/pcn123.pdf", "kind": "pcn",
              "title": "…", "status": "queued"}]}
```

`request_part_document(part_id_or_mpn, url, kind, title?)` validates the URL's
shape only — https, a public domain name (no IP address, no `localhost` or
internal suffix), no credentials, the default port, ≤ 2048 characters — and
records it. A URL the part already has (its datasheet, a media link, an earlier
request) answers `queued: false` with the existing entry. At most
`DOCUMENT_REQUEST_PENDING_CAP` (200) requests may wait per workspace. Link the
PDF itself, not the HTML page around it: a page fails with `unexpected_type`.
Nothing extracts text yet.

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
| `list_substitutes` | `part_id_or_mpn` | — | `part.not_found` |
| `get_part_policy` | — | — | — |
| `validate_part` | — | same as `create_part` | `category.not_found`; `category.archived`; `part.invalid_field`. An unmet rule is `ok: false`, not a refusal |
| `part_policy_status` | `part_id_or_mpn` | — | `part.not_found` |
| `export_parts` | — | `cursor`, `page_size`, `include_archived` | `mcp.invalid_cursor` (a cursor this tool did not mint — start over without one) |
| `check_pin_pad` | `part_id_or_mpn` | — | `part.not_found`. A missing, external or unreadable file is `verdict: not-checkable` with a `reason`, not a refusal |
| `list_pin_pad_issues` | — | `verdict`, `cursor`, `page_size` | `mcp.invalid_cursor` (a cursor this tool did not mint) |
| `list_part_documents` | `part_id_or_mpn` | `kinds` | `part.not_found`; `document.invalid_kind` |
| `list_spice_binding_issues` | — | `cursor`, `page_size` | `mcp.invalid_cursor` |
| `request_part_document` | `part_id_or_mpn`, `url`, `kind` | `title` | `part.not_found`; `document.invalid_url`; `document.invalid_kind`; `document.invalid_title` (> 300 characters); `document.queue_full` (200 requests already waiting in this workspace — wait for the hourly job) |
| `catalog_lookup` | `mpn` | `manufacturer`, `provider` | `part.provider_not_configured`; `part.provider_missing_mpn`; when NO provider answered, the first provider's `provider.quota_reserve_reached` / `provider.suspended` / `provider.pacing` / `rate_limited` / `provider.upstream_error`. A provider that refuses while another answers is listed under `errors`, not refused |
| `catalog_search` | `keywords` (unless `list_categories`) | `category_id`, `manufacturer_ids`, `parameter_filters`, `status`, `in_stock`, `rohs`, `has_datasheet`, `sort`, `limit`, `offset`, `provider`, `list_categories` | `provider.invalid_filter`; `provider.unsupported_filter`; `part.provider_not_configured`; and, when no provider answered, the same provider codes as `catalog_lookup` |
| `sourcing_offers` | `part_id` | `qty` | `part.not_found` only. Every other failure is **degradation, not a refusal**: a part with no MPN, unconfigured sourcing, an exhausted budget and a provider error all return `status` (`no_mpn` / `not_configured` / `budget_blocked` / `provider_error`) with an empty `offers` list. Read that as "unknown", not "unavailable" |
| `create_part` | one of `name` / `mpn` | `manufacturer`, `description`, `category_id`, `part_type`, `internal_part_number`, `footprint`, `notes_markdown` | `part.name_or_mpn_required`; `category.not_found`; `category.archived`; `part.invalid_field` (over-long value). **Not** `part.mpn_conflict` and **not** `part.policy_violation` — both are results, see above |
| `set_part_category` | `part_id_or_mpn`, `category_id_or_name` | — | `part.not_found`; `category.not_found` (lists up to 10 existing names); `category.name_conflict` when two differ only in case |
| `set_part_specs` | `part_id_or_mpn`, `specs` | `replace_missing` | `custom_field.reserved_key`; `custom_field.key_whitespace`; `custom_field.too_many` (> 50 keys); `custom_field.too_long` (key > 256, value > 1024) |
| `create_category` | `name` | `description` | `category.name_conflict`, `category.slug_conflict` |
| `add_stock` | `part_id`, `qty` | `storage_location_id`, `note` | `stock.operation_error` — a non-positive `qty`, a location that is archived, full or not in this workspace, or the mandatory-default rule below; `stock.constraint_violation` for `single_part_only` / `existing_parts_only` |
| `consume_stock` | `part_id`, `qty` | `storage_location_id`, `note` | `stock.operation_error` — a non-positive `qty`, or "insufficient stock (have 3, want 10)" |
| `move_stock` | `part_id`, `qty`, `from_location_id`, `to_location_id` | — | `resource.not_found` for either end; `stock.operation_error` for a non-positive `qty` or too little stock at the source; `stock.constraint_violation` on the destination |
| `set_part_eda` | `part_id` | the other eleven | `eda_symbol.not_found`, `eda_footprint.not_found`, `eda_datafile.not_found`; `eda.ref_conflict` when both `<slot>_id` and `<slot>_ref_external` are set; `eda.archived` for a retired entry |
| `upload_eda_asset` | `kind`, `filename`, `content_base64` | `part_id`, `category_slug` | `eda.spice_export_schema_unsupported` (a SPICE file whose ngSPICE export header names an unknown schema), `eda.unsupported_kind`, `eda.invalid_file`, `eda.empty_file`, `eda.file_too_large`, `eda.multiple_symbols`, `eda.legacy_format` |
| `set_spice_binding` | `part_id_or_mpn` | `pin_map` (required for `file`), `model_kind`, `subckt`, `symbol_pins`, `unconnected_ports`, `device_type`, `params`, `fallback`, `fallback_token`, `native`, `positive_pin`, `export_reason`, `native_reason`, `source` | `part.not_found`; `eda.spice_model_missing`; `eda.file_not_found`; `eda.spice_no_model`; `eda.spice_subckt_ambiguous` / `eda.spice_subckt_unknown` (both list `available`); `eda.spice_model_not_checkable`; `eda.spice_symbol_pins_unknown`; `eda.spice_symbol_pins_conflict`; `eda.spice_pin_unknown`; `eda.spice_pin_unmapped`; `eda.spice_port_unknown`; `eda.spice_port_duplicate`; `eda.spice_port_count_mismatch`; `eda.spice_sim_pins_too_long`; `eda.spice_kind_conflict`; `eda.spice_not_applicable`; `eda.spice_field_too_long`; `eda.spice_export_schema_unsupported`; `eda.spice_fallback_token_unknown`; `eda.spice_native_invalid` |
| `set_asset_metadata` | `part_id_or_mpn`, `kind` | `origin`, `model_kind`, `simulators`, `valid_for`, `not_valid_for`, `note` | `part.not_found`; `eda.asset_not_attached` (slot empty or external); `eda.asset_metadata_invalid`; `eda.review_requires_owner` for `review_state` / `review_against` |
| `set_substitute_evaluation` | `part_id_or_mpn`, `substitute_id_or_mpn` | `relation`, `evaluation`, `evidence` (at least one) | `part.not_found`; `part.substitute_not_found` (no link between the two in this workspace; a one-way link only matches part → substitute); `part.substitute_invalid` (bad value, evidence > 500 characters, nothing to set) |
| `import_vendor_zip` | `part_id`, `content_base64` | `overwrite` | `eda.invalid_archive`, `eda.archive_too_large`, `eda.no_entries` |
| `fetch_lcsc` | `part_id`, `lcsc_id` | `overwrite` | `eda.lcsc_not_found`, `eda.lcsc_unavailable` |
| `sync_project_bom` | `project_id`, `rows` | `dry_run` | `project.not_found`; `bom.sync_invalid_row`; `bom.sync_duplicate_line`; `bom.sync_unkeyed_row`. Same diff as `POST /api/projects/{id}/bom/sync` — see [projects.md](./projects.md#post-apiprojectsproject_idbomsync) |

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

`catalog_lookup` is in the write column for the same reason and writes no
row at all: a cache miss spends a call from the DigiKey or Mouser daily
allowance the owner's own lookups depend on.

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

Codes specific to the provider tools:

| Code | Meaning | Retry? |
|---|---|---|
| `provider.quota_reserve_reached` | Fewer calls than the reserve remain today; the rest is the owner's | Not today |
| `provider.suspended` | The provider has blocked this application (DigiKey 403); the message says how long | After the stated wait |
| `provider.pacing` | Less than the minimum spacing since this workspace's previous call to that provider; carries `retry_after_ms` | After `retry_after_ms` |
| `provider.upstream_error` | The provider failed or rejected the credentials | Later |
| `rate_limited` | The provider answered 429 | Later |
| `part.provider_not_configured` | No credentials for the provider asked for, or none at all | No — ask the owner |
| `provider.unsupported_filter` | `catalog_search`: a filter the provider cannot apply (Mouser: anything but keywords / in_stock / rohs) | No — drop it or use DigiKey |
| `provider.invalid_filter` | `catalog_search`: no keywords, or parametric filters without `category_id` | No — fix the call |
| `eda.lcsc_disabled` | `fetch_lcsc` is parked for agents ([Disabling](#disabling)) | No |
| `document.queue_full` | This workspace already has 200 requested documents waiting for the hourly download | After the job has drained some |

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
| `export_parts` page size | 200 (clamped; paged, never truncated) |
| `list_pin_pad_issues` page size / parts scanned per call | 100 (clamped) / 500 |
| `list_spice_binding_issues` page size | 100 (clamped; paged) |
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

`set_spice_binding` has no REST twin; it records `part_spice_binding.set`
(`subckt=…,ports=N,sha256=…`). An `upload_eda_asset` that records or
unvalidates a binding adds `part_spice_binding.model_recorded`
(`sha256=…,validated=false`).

`set_asset_metadata` has no REST twin; it records `eda_asset_metadata.set`
(`kind=…,sha256=…,fields=…` — field names, never values). A review from the
app records `eda_asset_review.set` (`kind=…,sha256=…,state=…`).

`set_substitute_evaluation` records `part.substitute_evaluated` with both
ends of the link as targets, the REST twin `PATCH
/api/parts/{id}/substitutes/{sub}` the same; the comment is
`fields=…;relation=…;evaluation=…` — field names and the new enumerated
state, never the `evidence` text. A call that changed nothing writes none.

`set_part_specs` has no REST twin — one call writes many rows — so it records
one `part.specs_updated` row naming the keys that moved and never their values.
A call that changed nothing writes none. `create_part` on an existing MPN
writes none either: nothing happened, and the trail must not say a part was
created.

## Disabling

`MCP_ENABLED=false` in the backend environment removes the mount entirely, so
`/mcp` 404s like any unrouted path. It is the kill switch for an agent
integration that needs stopping without redeploying the app.

`MCP_FETCH_LCSC_ENABLED` (default **false**) gates the `fetch_lcsc` tool.
EasyEDA's terms prohibit access "by any robot, spider, scraper, crawler,
script or other automated means" without written permission, and the owner
has parked agent use until that is settled. While false the tool stays
listed, with the same name and arguments, and refuses with
`eda.lcsc_disabled` before contacting anything. The REST route behind the
UI's LCSC button (`POST /api/parts/{part_id}/eda/fetch-lcsc`) is an owner's
own action and does not read the flag.

## See also

- [agents](agents.md) — driving the REST API directly with the same token
- [tokens](tokens.md) — minting, revoking, the token model
- [ADR-0030](../adr/0030-mcp-server-surface.md) — why in-process, why stateless, why the service layer
- [ADR-0029](../adr/0029-api-tokens-and-csrf-exemption.md) — the credential and the CSRF exemption
- `backend/app/mcp/README.md` — the module map
- `backend/tests/test_mcp.py`, `backend/tests/test_mcp_parts.py`,
  `backend/tests/test_mcp_catalog.py` — this page, executable
