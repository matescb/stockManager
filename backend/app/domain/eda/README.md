# eda

Audience: engineer

Owns the workspace's KiCad library — schematic symbols, PCB footprints, 3D models and SPICE models — plus `PartEda`, the 1:1 row saying which of them a part uses. `kicad_library.py` serves it over the KiCad HTTP-library protocol at `/kicad-api/v1`, and `kicad_refs.py` holds the names that surface and the phase-6 file generation must agree on.

## Files

| File | What |
|---|---|
| `models.py` | `EdaSymbol`, `EdaFootprint`, `EdaDatafile`, `EdaFootprintModel`, `PartEda` |
| `schemas.py` | `Eda*Patch` / `Eda*Out`, `EdaFootprintModelIn/Out`, `PartEdaIn/Out` |
| `service.py` | List / upload / rename / archive / restore, model links, part config |
| `storage.py` | The text-CAD upload lane: per-kind validation + content-addressed writes |
| `sexpr.py` | KiCad s-expression reader/writer (no dependency) |
| `vendor_zip.py` | Reads a SnapEDA / SamacSys / UltraLibrarian zip into an `ImportPlan` |
| `lcsc.py` | The `easyeda2kicad` seam — one LCSC part fetched and converted into the same plan |
| `importer.py` | Turns a plan into rows, blobs and part wiring |
| `kicad_refs.py` | The naming contract: file stem `SM_<slug>`, reference `PCM_SM_<slug>:<entry>`, `${STOCKMGR_3D}/…`, `${STOCKMGR_SPICE}/…` |
| `kicad_library.py` | The `/kicad-api/v1` documents: eligibility, the one-query listing, JSON shaping |

## Public surface

| Operation | Entry point |
|---|---|
| List / fetch a library entry | `service.py::list_entries`, `::get_entry` |
| Record an upload (dedup + conflict) | `service.py::upload_entry` |
| Rename / re-file, archive, restore | `service.py::update_entry`, `::archive_entry`, `::restore_entry` |
| Attach / detach a 3D model | `service.py::link_footprint_model`, `::unlink_footprint_model` |
| Read / write / drop a part's config | `service.py::get_part_eda`, `::upsert_part_eda`, `::delete_part_eda` |
| Validate + store an uploaded file | `storage.py::canonical_symbol`, `::canonical_footprint`, `::validated_datafile`, `::store` |
| Parse / emit / edit KiCad s-expressions | `sexpr.py::parse`, `::emit`, `::entries`, `::rename`, `::set_property`, `::rewrite_model_paths` |
| Read a vendor archive | `vendor_zip.py::read_archive`, `::read_symbol_library`, `::narrow_to_part` |
| Fetch + convert an LCSC part | `lcsc.py::fetch_plan` |
| Write a plan down | `importer.py::import_plan`, `::wire_part` |
| Build a KiCad reference | `kicad_refs.py::library_nickname`, `::symbol_ref`, `::footprint_ref`, `::model_path`, `::spice_path` |
| Serve the KiCad library | `kicad_library.py::root_document`, `::list_categories`, `::list_parts`, `::part_detail` |

REST surface: `backend/app/api/routes/eda.py` (library CRUD + per-part config) and
`backend/app/api/routes/eda_import.py` (the three import endpoints), both mounted
under `/api/eda` and `/api/parts/{part_id}/eda`; plus
`backend/app/api/routes/kicad.py` at `/kicad-api/v1`, which is outside `/api`,
outside the envelope and authenticated by a personal access token only.

## Hard rules (this module)

1. **Uploads take this module's lane, not the attachment validators.** `attachments.py::_detect_mime` and `parts/services/assets.py::_sniff_ext` allow-list binary formats by magic bytes and must not be loosened — every format they accept is one a browser will render. KiCad libraries are text with no magic number, so `storage.py` validates them structurally instead (it parses them) and nothing here is ever served inline.
2. **A symbol or footprint file is immutable.** It's content-addressed at `{UPLOAD_DIR}/eda/{ws_id}/{sha256}.{ext}`; "editing" one means uploading new bytes. A PATCH moves the metadata only, which is why `sha256` never appears in a patch schema.
3. **Names are unique per workspace among active rows** — partial unique indexes `uq_eda_symbols_ws_name`, `uq_eda_footprints_ws_name`, `uq_eda_datafiles_ws_kind_name` (`WHERE archived_at IS NULL`, alembic 0068), the same shape `part_categories` uses. Archiving frees a name, so `restore_entry` re-checks and returns `409` with `existing_id`.
4. **`part_eda` slots are exclusive.** `symbol_id` (a definition we host) and `symbol_ref_external` (a `LibNick:Entry` in the user's own libraries) never coexist — same for the footprint pair. Enforced in `service._resolve_ref` as a 422 and by the `ck_part_eda_*_ref_exclusive` CHECK constraints.
5. **`PUT /api/parts/{id}/eda` replaces, it does not merge.** An omitted field is written as its default. It's the only way "clear the symbol" is expressible, and the CAD tab posts the whole form on every save.
6. **One symbol per *upload*.** A multi-symbol `.kicad_sym` is a 422 (`eda.multiple_symbols`) on `POST /api/eda/symbols`, not a silent "took the first" — that file goes to `POST /api/eda/import`, and the error message says so.
7. **An import suffixes on a name conflict; an upload 409s.** A single upload can hand the 409 back and let the user rename. An archive can't — refusing six files because one name is taken is useless — so `importer._store_entry` walks ` (2)`…` (9)` and only then lets the 409 through. The suffix renames the **s-expression as well as the row**: a symbol carries its entry name inside the file, so `_store_entry` re-renders the bytes for every candidate name. A row called `MYPART (2)` holding bytes that say `(symbol "MYPART")` breaks the same invariant `service._rewrite_stored_entry_name` exists to hold. For a DATA file the suffix goes before the extension (`P (2).step`, never `P.step (2)`): KiCad picks its 3D plugin by extension, and the row name is what the rewritten `(model …)` path points at.
8. **A bad member is a note, never a failed archive.** Anything that fails validation lands in the response's `skipped` list with a reason. The four whole-archive rejections are deliberate and few: not a zip (`eda.invalid_archive`), past the member/size caps (`eda.archive_too_large`), KiCad-5-only (`eda.legacy_format`), and genuine ambiguity about which symbol or footprint belongs to the part (`eda.multiple_symbols` / `eda.multiple_footprints`).
9. **Model paths are rewritten before the footprint is stored.** A vendor footprint points at the vendor's own tree; `importer._rewrite_models` repoints it at `${STOCKMGR_3D}/<row name>` (phase 6 substitutes the variable) and DROPS a `(model …)` whose file wasn't in the archive. The stored bytes are what phase 6 packages, so the rewrite has to happen before `storage.canonical_entry_bytes`.
10. **An import fills empty slots only.** `overwrite` is opt-in, and `value`, `keywords` and the exclusion flags are never touched — no vendor archive knows better than the user.
11. **Three budgets bound an archive, and the parse budget is the one that bounds memory.** `MAX_MEMBERS` and `MAX_UNCOMPRESSED_BYTES` are checked against the central directory, which a hostile zip simply lies about — so `_Budget.inflate` re-checks the real total as chunks come out of zlib, and `MAX_PARSED_TEXT_BYTES` caps what reaches the s-expression reader. A parsed node tree runs ~20x its source text; without that last cap a ~130 KiB upload peaked at 1.2 GiB RSS. All three are enforced INSIDE the member walk — trimming a finished list means everything was parsed and retained first, which is the cost the caps exist to avoid.
12. **One module owns every name KiCad sees.** `kicad_refs.py` — one generated library per category, whose FILE stem is `SM_<library_slug>` (`SM_uncategorized` for rows with none) but whose NICKNAME is `PCM_SM_<library_slug>`, because KiCad's Plugin & Content Manager prepends `PCM_` when it registers an installed package's libraries. Every `LibNick:Entry` reference is built on the nickname; one built on the bare stem names no registered library and KiCad reports a broken symbol on every part that uses it ([forum.kicad.info/t/…/63784](https://forum.kicad.info/t/pcm-content-library-and-library-prefix-problem/63784)). The slug comes from the SYMBOL's or FOOTPRINT's own category, not the part's. Models are `${STOCKMGR_3D}/<name>` and SPICE is `${STOCKMGR_SPICE}/<name>`. The HTTP library serves these strings and phase 6 generates the files they name; a second copy of the format anywhere is how those two drift apart.
13. **`/kicad-api` collapses every failure it can distinguish into one 404.** The KiCad client accepts no status but 200 and shows no error body, so a missing token, a revoked one, an unknown category, a foreign part and a part with no symbol are one indistinguishable failure. The rate limiter's 429 is the deliberate exception: slowapi raises it before this router's code runs, it is reachable without a valid credential (so it is no oracle), and flattening it would cost the caller `Retry-After`. Session cookies never authenticate this surface. Both rate-limit buckets — a digest of the presented token, and the caller's IP — are checked on EVERY request including ones that never authenticate, which is why the routes resolve the token in their body rather than in a dependency (dependencies resolve before slowapi's wrapper runs).
14. **Every scalar `/kicad-api` emits is a string**, booleans (`"True"` / `"False"`) and ids included. `tests/test_kicad_api.py::test_no_non_string_scalars_anywhere` walks the documents, so a new field can't quietly ship an int.
15. **Every path an upstream name touches is sanitised before it is used.** `easyeda2kicad` builds its output path from EasyEDA's own JSON title, so `lcsc._rename_model` flattens the name before the exporter runs, and `_read_converted` re-checks with `os.path.realpath` that what came back is still inside the conversion directory.

## See also

- [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md) — workspace isolation is the caller's job
- [categories](../categories/README.md) — `category_id` on symbols and footprints points here
- [api/routes README](../../api/routes/README.md) — router → docs map

## Don't

- Don't parse KiCad files with a third-party binding. `kiutils` lags the format and fails on nodes it hasn't been taught — exactly the wrong behaviour for a user's upload. `sexpr.py` preserves what it doesn't understand.
- Don't make `sexpr.parse` recursive. It takes uploads, and a deeply nested file has to be a 422, not a `RecursionError` behind a 500.
- Don't drop the `Quoted` marker when touching `sexpr.py`. The bare token `yes` and the string `"yes"` mean different things to KiCad, and collapsing them corrupts round-tripped files.
- Don't hard-delete a library entry to "clear" it from parts — archive it, so the audit trail and the `SET NULL` references survive.
- Don't shell out to `easyeda2kicad`'s CLI. `lcsc.py` drives its converter classes directly; the CLI would mean a subprocess, a writable working directory and no way to bound the fetch.
- Don't fold case when detecting a vendor. `KiCad/` is SamacSys and `KiCAD/` is UltraLibrarian — one letter apart, and the only discriminator either archive carries.
- Don't parse an archive on the event loop. Inflating and re-emitting up to 200 entries is CPU-bound, prod runs one uvicorn worker, and `run_in_threadpool` is what keeps the rest of the API answering.
- Don't decode an archive member with a bare `raw.decode()`. Use `storage.decode_text` — a lone NUL is valid UTF-8 and reaches Postgres as a DataError 500 rather than a 422.
- Don't union the evidence when picking which symbol or footprint belongs to a part. `vendor_zip._pick_*` tries tiers in order and the first that resolves wins; unioning lets a filename hint veto a footprint reference the vendor wrote down explicitly.
- Don't give the content-addressed write a scratch name derived only from the hash. Two concurrent imports of the same bytes share the target, and a shared `.tmp` meant the first `os.replace` pulled the file out from under the second.
- Don't let an entry disappear between the archive and the response. Anything not imported — a member we couldn't place, a `(model …)` whose file was missing, a library entry narrowed away from the part — is a `skipped` note naming it.
- Don't set the route's `asyncio.wait_for` to `lcsc.FETCH_BUDGET_SECONDS`. The outer wait needs headroom (`HARD_TIMEOUT_SECONDS`) or it fires first every time and the per-stage deadline checks become dead code.
