# eda

Audience: engineer

Owns the workspace's KiCad library — schematic symbols, PCB footprints, 3D models and SPICE models — plus `PartEda`, the 1:1 row saying which of them a part uses. Phase 5 serves this over the KiCad HTTP-library protocol; nothing else in the app reads it yet.

## Files

| File | What |
|---|---|
| `models.py` | `EdaSymbol`, `EdaFootprint`, `EdaDatafile`, `EdaFootprintModel`, `PartEda` |
| `schemas.py` | `Eda*Patch` / `Eda*Out`, `EdaFootprintModelIn/Out`, `PartEdaIn/Out` |
| `service.py` | List / upload / rename / archive / restore, model links, part config |
| `storage.py` | The text-CAD upload lane: per-kind validation + content-addressed writes |
| `sexpr.py` | KiCad s-expression reader/writer (no dependency) |

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

REST surface: `backend/app/api/routes/eda.py` (`/api/eda` and `/api/parts/{part_id}/eda`).

## Hard rules (this module)

1. **Uploads take this module's lane, not the attachment validators.** `attachments.py::_detect_mime` and `parts/services/assets.py::_sniff_ext` allow-list binary formats by magic bytes and must not be loosened — every format they accept is one a browser will render. KiCad libraries are text with no magic number, so `storage.py` validates them structurally instead (it parses them) and nothing here is ever served inline.
2. **A symbol or footprint file is immutable.** It's content-addressed at `{UPLOAD_DIR}/eda/{ws_id}/{sha256}.{ext}`; "editing" one means uploading new bytes. A PATCH moves the metadata only, which is why `sha256` never appears in a patch schema.
3. **Names are unique per workspace among active rows** — partial unique indexes `uq_eda_symbols_ws_name`, `uq_eda_footprints_ws_name`, `uq_eda_datafiles_ws_kind_name` (`WHERE archived_at IS NULL`, alembic 0068), the same shape `part_categories` uses. Archiving frees a name, so `restore_entry` re-checks and returns `409` with `existing_id`.
4. **`part_eda` slots are exclusive.** `symbol_id` (a definition we host) and `symbol_ref_external` (a `LibNick:Entry` in the user's own libraries) never coexist — same for the footprint pair. Enforced in `service._resolve_ref` as a 422 and by the `ck_part_eda_*_ref_exclusive` CHECK constraints.
5. **`PUT /api/parts/{id}/eda` replaces, it does not merge.** An omitted field is written as its default. It's the only way "clear the symbol" is expressible, and the CAD tab posts the whole form on every save.
6. **One symbol per upload.** A multi-symbol `.kicad_sym` is a 422 (`eda.multiple_symbols`), not a silent "took the first" — that file needs the zip importer landing in phase 3.

## See also

- [ADR-0002](../../../../docs/adr/0002-code-enforced-workspace-isolation.md) — workspace isolation is the caller's job
- [categories](../categories/README.md) — `category_id` on symbols and footprints points here
- [api/routes README](../../api/routes/README.md) — router → docs map

## Don't

- Don't parse KiCad files with a third-party binding. `kiutils` lags the format and fails on nodes it hasn't been taught — exactly the wrong behaviour for a user's upload. `sexpr.py` preserves what it doesn't understand.
- Don't make `sexpr.parse` recursive. It takes uploads, and a deeply nested file has to be a 422, not a `RecursionError` behind a 500.
- Don't drop the `Quoted` marker when touching `sexpr.py`. The bare token `yes` and the string `"yes"` mean different things to KiCad, and collapsing them corrupts round-tripped files.
- Don't hard-delete a library entry to "clear" it from parts — archive it, so the audit trail and the `SET NULL` references survive.
