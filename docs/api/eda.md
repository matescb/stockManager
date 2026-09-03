# EDA API

Audience: engineer

`/api/eda` — the workspace's KiCad library (symbols, footprints, 3D models, SPICE models), the per-part configuration that names entries from it, and the importers that fill it from a vendor zip or LCSC.

## Conventions

See [API conventions](./README.md) for envelope, errors, pagination.

Two routers, mounted four times (`backend/app/main.py:588-613`), all four with `dependencies=_member_gate`:

| Router | Prefix |
|---|---|
| `eda.router` | `/api/eda` |
| `eda.parts_router` | `/api/parts` |
| `eda_import.router` | `/api/eda` |
| `eda_import.parts_router` | `/api/parts` |

`_member_gate` (`backend/app/main.py:547`) lets `GET` / `HEAD` / `OPTIONS` through for any role and answers `403` `resource.insufficient_role` to a viewer on anything else (`backend/app/core/deps.py:514-532`). So every read below is open to viewers; every write needs member+.

Rate limits bucket per workspace (`key_func=workspace_key`), and are live only in prod ([ADR-0012](../adr/0012-uvicorn-single-worker-slowapi.md)): uploads `20/minute` (`backend/app/api/routes/eda.py:68`), configuration `60/minute` (`eda.py:69`), imports `10/minute` (`backend/app/api/routes/eda_import.py:61`), LCSC `5/minute` (`eda_import.py:63`), 2D previews `120/minute` (`eda.py:740`).

Three routes on this surface do **not** return the envelope: `GET /api/eda/files/{ws_id}/{filename}` streams a `FileResponse`, and the two [2D preview](#2d-previews) routes return a raw KiCad document. The KiCad-facing surfaces live outside `/api` entirely — see [kicad](kicad.md).

Cross-workspace and unknown ids are `404`, never `403` (`backend/app/domain/eda/service.py:218-236`). Codes: `eda_symbol.not_found`, `eda_footprint.not_found`, `eda_datafile.not_found`, `part.not_found` (`backend/app/core/errors.py:273-275`, `:225`).

## The library

Symbols, footprints and data files are three tables with one shape, so their routes are near-identical. What differs: only symbols and footprints carry a `category_id`; only data files carry a `kind`. See [domain/eda](../domain/eda.md) for the model.

### `GET /api/eda/symbols`

List the workspace's symbols, ordered by name.

**Request** — query parameters

| Field | Type | Required | Notes |
|---|---|---|---|
| `include_archived` | bool | no | Default `false`. |
| `limit` | int | no | Default `200`, max `1000`. |

**Response** — `200 OK`

```json
{ "data": [{ "id": "…", "name": "R_10k", "sha256": "…", "size_bytes": 812,
             "source": "manual", "category_id": "…", "archived_at": null }],
  "status": { … } }
```

`source` is server-controlled and is one of `manual`, `snapeda`, `samacsys`, `ultralibrarian`, `easyeda` (`backend/app/domain/eda/models.py:25`).

**Notes**

- Source: `backend/app/api/routes/eda.py:78-87`
- Service: `backend/app/domain/eda/service.py:200-215`

### `GET /api/eda/footprints`, `GET /api/eda/datafiles`

Same parameters. Footprints return the same body as symbols; data files return `kind` (`step` | `wrl` | `spice`) instead of `category_id`, and are ordered by `(kind, name)`.

**Notes**

- Source: `backend/app/api/routes/eda.py:231-241`, `eda.py:458-468`

### `POST /api/eda/symbols`

Upload one `.kicad_sym` holding exactly one symbol.

**Request** — `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `file` | file | yes | A `.kicad_sym` document, or a bare `(symbol …)` node. |
| `name` | string | no | Max 200. Defaults to the name parsed out of the file. |
| `category_id` | uuid | no | Must be an active category in this workspace. |

**Response** — `201 Created`, or **`200 OK`** when the upload deduped onto an existing row (`eda.py:146`). The store is content-addressed, so re-uploading identical bytes under the same name returns the existing row untouched — no audit row, and **a `category_id` sent on that path is not applied**; re-filing an entry is a `PATCH` (`service.py:252-263`).

**Errors**

| Status | Code | Condition |
|---|---|---|
| 413 | `eda.file_too_large` | Input over the per-kind cap. Carries `max_bytes`. |
| 422 | `eda.empty_file` | Zero bytes. |
| 422 | `eda.invalid_file` | Not UTF-8, contains NULs, unparseable, or the wrong root token. |
| 422 | `eda.multiple_symbols` | More than one symbol in the file. Carries `symbol_count` and `symbol_names` (first 20, each ≤80 chars). Import it as a library instead. |
| 422 | `eda.file_too_large` | The **re-emitted canonical form** busts the cap (`backend/app/domain/eda/storage.py:161-181`). |
| 409 | `eda.name_conflict` | An active row holds this name with different bytes. Carries `existing_id`, `existing_name` — except on the lost-race path, which carries neither (`service.py:159-171`). |
| 404 | `category.not_found` | Unknown or foreign `category_id`. |
| 409 | `category.archived` | The named category is archived and the value is changing. Carries `existing_id`. |

Caps live in `MAX_BYTES_BY_KIND` (`storage.py:88-92`): symbol 1 MiB, footprint 2 MiB, SPICE 1 MiB; STEP and WRL fall through to `settings().MAX_UPLOAD_BYTES` (10 MiB).

**Notes**

- Source: `backend/app/api/routes/eda.py:90-147`
- Validation: `backend/app/domain/eda/storage.py:184-222`
- Audit: `eda_symbol.uploaded`, comment `sha256=<sha>` — only on the 201 path.

### `POST /api/eda/footprints`

Same shape. Accepts `.kicad_mod`; the pre-6.0 `(module …)` spelling parses too (`storage.py:237`). Audit `eda_footprint.uploaded`.

### `POST /api/eda/datafiles`

Same shape minus `category_id`. `kind` is derived from the filename extension, never from the bytes — the three formats share no reliable discriminator (`eda.py:484-486`):

| Extension | `kind` | Stored as |
|---|---|---|
| `.step`, `.stp` | `step` | `.step` |
| `.wrl`, `.vrml` | `wrl` | `.wrl` |
| `.lib`, `.sub`, `.cir`, `.mod`, `.spice` | `spice` | `.lib` |

Anything else is `422` `eda.unsupported_kind`. STEP must start `ISO-10303-21` and WRL `#VRML` (`storage.py:97-98`); SPICE is only checked for valid UTF-8, because the simulator is the arbiter of what is in it. Audit `eda_datafile.uploaded`, comment `kind=<kind>,sha256=<sha>`.

### `PATCH /api/eda/{symbols,footprints,datafiles}/{id}`

Rename, and for symbols and footprints re-file into a category.

**Request**

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | no | Min 1, max 200. Explicitly `null` is `422` `eda.field_not_nullable`. |
| `category_id` | uuid \| null | no | Symbols and footprints only; `null` clears it. |

`extra="forbid"` — an unknown key is a `422`.

**Notes**

- Renaming a symbol or footprint **rewrites the stored file**, because the name lives inside the s-expression. The rewrite lands at a new content hash, so `sha256` and `size_bytes` move with it and the old blob stays on disk unreferenced ([ADR-0005](../adr/0005-content-addressed-assets.md), `service.py:353-378`). Data files carry no embedded name and are not rewritten.
- Renaming an **archived** entry skips the collision check — an archived entry holds no name, and the conflict surfaces at restore instead (`service.py:331-341`).
- Source: `backend/app/api/routes/eda.py:150-174`
- Audit: `eda_symbol.updated` / `eda_footprint.updated` / `eda_datafile.updated`, comment `fields=<sorted set>`.

### `POST /api/eda/{symbols,footprints,datafiles}/{id}/archive` and `/restore`

Soft-archive and un-archive. Both answer `200` with `data: null`.

The unique indexes are partial on `archived_at IS NULL`, so **archiving frees the name**. Archiving an already-archived row is a no-op; restoring re-checks the name and answers `409` `eda.name_conflict` if someone took it meanwhile (`service.py:392-417`).

**Notes**

- Source: `backend/app/api/routes/eda.py:176-223`, `eda.py:323-368`, `eda.py:562-607`
- Audit: `<entity>.archived` / `<entity>.restored`.

## 3D models on a footprint

### `GET /api/eda/footprints/{footprint_id}/models`

The footprint's 3D links, ordered by `position`. Returns `[{ "datafile_id": "…", "position": 0 }]`.

### `POST /api/eda/footprints/{footprint_id}/models`

Attach a 3D model. An **upsert**, not a create: re-linking an existing pair updates its `position` rather than conflicting (`service.py:445-449`).

**Request**

| Field | Type | Required | Notes |
|---|---|---|---|
| `datafile_id` | uuid | yes | Must be a `step` or `wrl` data file. |
| `position` | int | no | Default `0`, range 0–1,000,000. KiCad renders the first model. |

**Response** — `200 OK` with the footprint's **full** model list, not just the new link.

**Errors** — `422` `eda.unsupported_kind` when the data file is a SPICE model; `404` for an unknown footprint or data file.

### `DELETE /api/eda/footprints/{footprint_id}/models/{datafile_id}`

Detach. Unlinking a pair that was never linked answers `200` — `DELETE` is idempotent and the caller's intent holds either way (`service.py:493-497`). The footprint id must still exist.

**Notes**

- Both write audit `eda_footprint.updated` against the **footprint**, comment `model_linked=<id>` / `model_unlinked=<id>`.
- Linking touches the footprint's `updated_at` explicitly (`service.py:49-65`). That timestamp is what the [PCM package](kicad.md#the-pcm-repository) derives its version from, so without it a 3D change would never reach an installed copy.

## Per-part configuration

### `GET /api/parts/{part_id}/eda`

The part's EDA configuration, or `data: null` if it has none. Works on archived parts (`eda.py:915-918`).

**Response** — `200 OK`

```json
{ "data": { "part_id": "…", "symbol_id": "…", "symbol_ref_external": null,
            "footprint_id": "…", "footprint_ref_external": null,
            "spice_datafile_id": null, "value": "10k", "keywords": null,
            "footprint_filters": ["R_*"], "exclude_from_bom": false,
            "exclude_from_board": false, "exclude_from_sim": true,
            "sim_device": null, "sim_pins": null, "sim_params": null },
  "status": { … } }
```

### `PUT /api/parts/{part_id}/eda`

Replace the configuration, creating it if absent.

**A full replacement, not a merge.** Every field is optional and one the caller omits is written as its default — `null`, or `false`/`true` for the exclusion flags (`service.py:639-644`). That is what `PUT` means, it is what the CAD tab sends (the whole form on every save), and it is the only reading under which "clear the symbol" is expressible. A client that `PATCH`es by `PUT` — sending only `{"value": "10k"}` — wipes the symbol, footprint and filters.

**Request**

| Field | Type | Notes |
|---|---|---|
| `symbol_id` | uuid \| null | A symbol this workspace hosts. |
| `symbol_ref_external` | string \| null | Max 200. A `LibNick:Entry` into the user's own libraries. |
| `footprint_id` | uuid \| null | |
| `footprint_ref_external` | string \| null | Max 200. |
| `spice_datafile_id` | uuid \| null | Must be a `spice` data file. |
| `value` | string \| null | Max 120. |
| `keywords` | string \| null | Max 300. |
| `footprint_filters` | string[] \| null | Max 50 items, each 1–100 chars. |
| `exclude_from_bom` | bool | Default `false`. |
| `exclude_from_board` | bool | Default `false`. |
| `exclude_from_sim` | bool | Default **`true`** — no config means no simulation model, and KiCad errors on a symbol that claims simulability it lacks. |
| `sim_device` | string \| null | Max 60. |
| `sim_pins` | string \| null | Max 300. |
| `sim_params` | string \| null | Max 500. |

Each slot is named **either** by a hosted id or by an external ref, never both. Both null means "inherit the category default".

**Errors**

| Status | Code | Condition |
|---|---|---|
| 422 | `eda.ref_conflict` | Both halves of one slot set. Carries `slot` (`"symbol"` or `"footprint"`). |
| 409 | `eda.archived` | The referenced entry is archived. Carries `existing_id`. |
| 422 | `eda.unsupported_kind` | `spice_datafile_id` names a non-SPICE data file. |
| 404 | `part.not_found` | Unknown, foreign **or archived** part. |

**Notes**

- Source: `backend/app/api/routes/eda.py:923-955`
- Audit: `part_eda.updated`, target `part_eda`, target id the **part's** id, comment `fields=<sorted set>`.

### `DELETE /api/parts/{part_id}/eda`

Drop the configuration. A hard delete, not an archive (`service.py:647-654`). Deleting a configuration that does not exist is a `200`, not a `404`.

## Importers

Three entry points, one plan. `vendor_zip.py` and `lcsc.py` decide what an archive or an LCSC part offers, touching no database and no filesystem; `importer.py` is the only thing that writes it down. See [domain/eda § import pipeline](../domain/eda.md#the-import-pipeline).

There is no dry-run endpoint — every import parses, writes, then responds.

### `POST /api/parts/{part_id}/eda/import`

Import a vendor archive and wire what it found to this part.

**Request** — `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `file` | file | yes | A vendor zip. Capped at `MAX_UPLOAD_BYTES` (10 MiB). |
| `overwrite` | bool | no | Default `false`. |
| `category_id` | uuid | no | Filed onto the symbols and footprints created. |

`overwrite=false` fills **empty slots only** — an import is additive by default, and a slot naming an external `LibNick:Entry` counts as occupied (`backend/app/domain/eda/importer.py:469-478`). `value`, `keywords` and the exclusion flags are never touched by an import.

**Response** — `200 OK`

```json
{ "data": { "vendor": "samacsys", "symbol": { "id": "…", "name": "…", "created": true },
            "footprint": { … }, "datafiles": [ … ], "part_eda_updated": true,
            "skipped": [{ "filename": "readme.txt", "reason": "unsupported file type" }] },
  "status": { … } }
```

At most one symbol and one footprint — the plan is narrowed to this part before anything is written. `created: false` means the row already held these exact bytes. `part_eda_updated` is `false` when nothing was written: either nothing was importable, or every target slot was occupied and `overwrite` was false.

**Vendor detection** (`backend/app/domain/eda/vendor_zip.py:224-231`) is by directory name and is **case-sensitive** — the capital `CAD` is the whole discriminator:

| Layout | `vendor` |
|---|---|
| a `KiCad/` directory (3D in a sibling `3D/`) | `samacsys` |
| a `KiCAD/` directory | `ultralibrarian` |
| flat root | `snapeda` |

The vendor only decides the `source` column; nothing about extraction branches on it, and an archive matching none of the shapes is still imported.

### `POST /api/eda/import`

Import an archive or a bare multi-symbol `.kicad_sym` into the library, wired to no part.

**Request** — `multipart/form-data`: `file` (required), `category_id` (optional). No `overwrite`. The format is sniffed from the leading bytes (`PK` → zip), not the extension (`eda_import.py:281-290`).

**Response** — `200 OK`

```json
{ "data": { "vendor": "manual", "created": 12, "reused": 3,
            "symbols": [ … ], "footprints": [ … ], "datafiles": [ … ], "skipped": [ … ] },
  "status": { … } }
```

A bare `.kicad_sym` reports `vendor: "manual"`.

### `POST /api/parts/{part_id}/eda/fetch-lcsc`

Fetch CAD data for an LCSC part number from EasyEDA and import it.

**Request** — JSON

| Field | Type | Required | Notes |
|---|---|---|---|
| `lcsc_id` | string | yes | Pattern `^C\d{1,10}$`, max length 11. |
| `overwrite` | bool | no | Default `false`. Same semantics as the zip import. |

**Response** — `200 OK`, the same body as the part-bound zip import, with `vendor: "easyeda"`.

**Errors**

| Status | Code | Condition |
|---|---|---|
| 404 | `eda.lcsc_not_found` | EasyEDA has no CAD data for the id. **A network failure looks the same** — the upstream client swallows it into the same empty response, so the message names both possibilities (`backend/app/domain/eda/lcsc.py:110-116`). |
| 502 | `eda.lcsc_unavailable` | EasyEDA unreachable, or the outer 30 s timeout fired. |

The fetch runs on a 20 s internal budget re-checked between stages, under a 30 s outer `asyncio.wait_for` (`lcsc.py:44-59`). Stages are independent: a part with no 3D model still yields its symbol, and the missing pieces are reported in `skipped`.

### Import errors and skips

Whole-archive rejections, all `422` (`vendor_zip.py:194-195`):

| Code | Condition | Extras |
|---|---|---|
| `eda.invalid_archive` | Not a readable zip or symbol library. | — |
| `eda.archive_too_large` | Over 200 members, or over 50 MiB uncompressed — checked against the central directory before anything is decompressed, and again against the real inflated size. | `max_members` / `max_bytes` |
| `eda.legacy_format` | The archive carries only a KiCad 5 `.lib` symbol library. The message names the fix: `kicad-cli sym upgrade <file>.lib`. | — |
| `eda.no_entries` | Nothing importable. | `skipped` (first 20) |
| `eda.multiple_symbols` | Part-bound only: several symbols and none unambiguously this part's. | `symbol_count`, `symbol_names` |
| `eda.multiple_footprints` | Part-bound only: several footprints and the symbol names none of them. | `footprint_count`, `footprint_names` |

Anything else that fails is a **note, not an error** — the member is listed in `skipped` and the rest still imports. The reason strings are stable and rendered verbatim by the frontend (`vendor_zip.py:124-136`, `importer.py:67-72`, `lcsc.py:68-73`), e.g. `unsupported file type`, `file exceeds the size limit for its type`, `legacy KiCad 5 symbol library — convert it with kicad-cli`, `library entry not wired to this part — import as a library to keep it`, `3D model reference dropped — the archive did not carry this file`.

**Audit** — an import writes one row per created entry, up to 20; past that it collapses to a single `eda_library.imported` row against the workspace with comment `vendor=…,symbols=…,footprints=…,datafiles=…` (`eda_import.py:106-132`). The two part-bound imports additionally always write `part_eda.imported` against the part.

## Client configuration

### `GET /api/eda/kicad-setup`

Everything needed to write a `.kicad_httplib` file, minus the token.

**Response** — `200 OK`

```json
{ "data": { "root_url": "https://…/kicad-api", "categories_ttl": 600, "parts_ttl": 60,
            "pcm_repository_url_template": "https://…/kicad-api/pcm/PASTE_YOUR_READONLY_TOKEN/repository.json",
            "pcm_package_identifier": "com.stockmanager.<32 hex>",
            "pcm_spice_path_variable": "STOCKMGR_SPICE",
            "pcm_spice_path_value": "${KICAD8_3RD_PARTY}/resources/com_stockmanager_<hex>/spice",
            "read_only_note": "…", "mcp_url": "https://…/mcp", "mcp_note": "…",
            "example": { "meta": { "version": 1.0 }, "name": "…", "source": { … } } },
  "status": { … } }
```

The plaintext of a token exists exactly once, in the response that minted it, so the server cannot hand out a ready-to-use file — `example.source.token` is the literal `PASTE_YOUR_TOKEN_HERE` and the settings page merges the real value in client-side (`web/src/routes/settings/KicadSetup.tsx`).

`example.meta.version` is a JSON **number**. Quoting it makes the file unloadable, which is why the frontend builds the download from a template rather than `JSON.stringify` (`KicadSetup.tsx::buildHttpLibFile`).

`mcp_url` is `null` when the server runs with `MCP_ENABLED=false`, which unmounts `/mcp` entirely ([ADR-0030](../adr/0030-mcp-server-surface.md)).

For the file format itself and the two KiCad refresh caveats, see [tokens § Using a token with KiCad](tokens.md#using-a-token-with-kicad). For what the URLs in this payload serve, see [kicad](kicad.md).

**Notes**

- Source: `backend/app/api/routes/eda.py:615-699`

## Serving stored files

### `GET /api/eda/files/{ws_id}/{filename}`

Stream a stored library file. `filename` is the content-addressed `{sha256}.{ext}`.

**Not the envelope** — this returns the bytes. `Cache-Control: public, max-age=31536000, immutable` (the content hash is in the URL), `X-Content-Type-Options: nosniff`, media type always `application/octet-stream`.

**Never inline.** A `.kicad_sym` is attacker-supplied text on our own origin; rendering it in a tab would be a same-origin XSS. `Content-Disposition: attachment` always; the optional `?name=` query (max 120) supplies a filename for the Save-As dialog, filtered to ASCII alphanumerics plus `. _ -` because Unicode blows up Starlette's latin-1 header encoding with a 500 (`eda.py:886-892`).

**Errors** — `404` `eda.file_not_found` when `ws_id` is not the caller's workspace or the file is absent; `400` `eda.invalid_filename` when the name carries a separator or a leading dot.

**Notes**

- Source: `backend/app/api/routes/eda.py:845-899`
- Layout: `{UPLOAD_DIR}/eda/{ws_id}/{sha}.{ext}` (`backend/app/domain/eda/storage.py:12`)

## 2D previews

### `GET /api/eda/symbols/{symbol_id}/preview.kicad_sch`
### `GET /api/eda/footprints/{footprint_id}/preview.kicad_pcb`

Return the entry as a KiCad document the in-browser viewer can render. The CAD tab embeds these; nothing else consumes them.

**Not the envelope** — these return a raw document, like `/files/` above.

**Why a wrapper exists.** KiCanvas, the viewer the frontend embeds, reads only `.kicad_sch`, `.kicad_pcb`, `.kicad_wks` and `.kicad_pro`. It has no reader for `.kicad_sym` or `.kicad_mod`, which is exactly what this domain stores — pointing it at `/api/eda/files/…` yields "Unknown file type". So a symbol is served inside a synthetic one-symbol schematic and a footprint inside a synthetic one-footprint board, with the stored bytes embedded verbatim (never re-emitted). `backend/app/domain/eda/preview.py` carries the full rationale and the two constraints that make the documents render rather than draw blank.

**The suffix is part of the contract.** KiCanvas types a document by the basename of its URL, so these paths cannot be renamed to something without a `.kicad_sch` / `.kicad_pcb` ending.

**Headers** — `Cache-Control: private, max-age=300`, `X-Content-Type-Options: nosniff`, media type `text/plain; charset=utf-8`. Unlike `/files/` these are deliberately *not* attachments: they are fetched by the viewer's JS, never saved. `private` rather than `public` because the URL is keyed by row id rather than by content, so what it returns changes when the entry is renamed or re-uploaded, and because the response is workspace-scoped.

**Archived entries preview.** `get_entry` includes them and the restore flow depends on it — deciding whether to bring an archived symbol back means seeing what it is. This surface is read-only, so showing it costs nothing.

**Errors** — `404` `eda_symbol.not_found` / `eda_footprint.not_found` for an unknown id or one in another workspace; `503` `eda.preview_unavailable` when the stored blob is missing or unparseable, which the content-addressed append-only store makes a "can't happen".

**Rate limit** — `120/minute` per workspace. Generous because the UI fires a preview on every selection change and the 300 s private cache absorbs remounts; it exists to bound the one thing these routes do that a list read does not, which is open a file and parse attacker-supplied s-expressions on the single uvicorn worker prod runs.

**A malformed wrapper is silent.** KiCanvas draws nothing and reports nothing when it cannot parse a document, so the wrapping is pinned from both ends: `backend/tests/test_eda_preview_fixtures.py` holds `preview.py` to documents checked in at `backend/tests/fixtures/eda/preview/`, and `web/src/components/eda/__tests__/kicanvasContract.test.ts` parses those same files with KiCanvas's real parsers. Change a builder → refresh the fixtures (the backend test says how) → re-run the vitest test, which is the half that proves the result still renders.

**Notes**

- Source: `backend/app/api/routes/eda.py` — the "2D preview documents" section
- Wrapping: `backend/app/domain/eda/preview.py`
- Viewer pin and its limitations: [kicanvas-provenance](../frontend/kicanvas-provenance.md)
- Why there are two KiCanvas builds: `web/test-vendor/kicanvas-parsers/README.md`

## See also

- [domain/eda](../domain/eda.md) — the tables, the storage lanes, the s-expression contract
- [kicad](kicad.md) — what KiCad itself talks to
- [tokens](tokens.md) — the credential those surfaces use
- [categories](categories.md) — per-category symbol/footprint defaults
- [agents](agents.md) — non-browser clients
