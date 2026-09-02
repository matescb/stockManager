"""Mutating MCP tools for the KiCad library: configuration and imports.

Every tool here is declared `writes=True`, which is what makes
`_registry.require_write` run before the body — read-only token first,
then viewer role. Getting that flag wrong is the failure mode this
module has to be read for, so `tests/test_mcp.py` asserts the write set
against the registry rather than leaving it to review.

Two contracts these tools share with their REST counterparts, and must
not drift from:

* **Audit.** Every mutation the REST routes record, these record too,
  with the same action name and the same comment grammar — and against
  the TOKEN OWNER, who is the person who authorised the agent.
* **Validation.** Uploaded bytes go through the same
  `domain/eda/storage.py` lane as the HTTP upload — canonicalised,
  magic-byte checked, size capped. The base64 transport changes how the
  bytes arrive, not what is accepted.

The inventory-side write tools are in `write_inventory.py`.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from app.domain.eda import importer, lcsc, storage, vendor_zip
from app.domain.eda import service as eda_service
from app.domain.eda.models import EdaDatafile, EdaFootprint, EdaSymbol
from app.domain.eda.schemas import PartEdaIn
from app.mcp.principal import Caller
from app.mcp.tools._registry import ToolError, tool
from app.mcp.tools._shared import (
    audit,
    compact,
    decode_base64,
    part_eda_payload,
    part_url,
    resolve_category,
    resolve_part,
    sid,
)

# Ceilings, matched to the REST twins in `api/routes/eda.py` and
# `api/routes/eda_import.py`, so a tool's cost does not depend on which
# door it came through. `_LCSC_RATE` is the tightest because the call
# reaches a third party we do not control.
_CONFIG_RATE = "60/minute"
_UPLOAD_RATE = "20/minute"
_IMPORT_RATE = "10/minute"
_LCSC_RATE = "5/minute"


# --------------------------------------------------------------------------
# CAD configuration
# --------------------------------------------------------------------------


@tool(writes=True, rate=_CONFIG_RATE)
def set_part_eda(
    caller: Caller,
    part_id: str,
    symbol_id: str | None = None,
    symbol_ref_external: str | None = None,
    footprint_id: str | None = None,
    footprint_ref_external: str | None = None,
    spice_datafile_id: str | None = None,
    value: str | None = None,
    keywords: str | None = None,
    footprint_filters: list[str] | None = None,
    exclude_from_bom: bool = False,
    exclude_from_board: bool = False,
    exclude_from_sim: bool = True,
) -> dict[str, Any]:
    """Set the CAD (KiCad) configuration for one part.

    This REPLACES the part's whole configuration — any field you omit is
    written as its default, not left alone. Read `get_part_eda` first
    and pass back what you want to keep.

    A symbol may be named in one of two ways and never both: `symbol_id`
    for a symbol hosted in this workspace's library, or
    `symbol_ref_external` for one in the user's own local KiCad
    libraries (a `LibNick:Entry` string such as "Device:R"). The same
    either-or applies to `footprint_id` / `footprint_ref_external`.
    Leaving both empty means the part inherits its category's default.

    Args:
        part_id: The part's id or exact MPN.
        symbol_id: Id of a symbol in this workspace's library.
        symbol_ref_external: A `LibNick:Entry` reference to a symbol in
            the user's local libraries.
        footprint_id: Id of a footprint in this workspace's library.
        footprint_ref_external: A `LibNick:Entry` reference to a
            footprint in the user's local libraries.
        spice_datafile_id: Id of a SPICE model file in this workspace's
            library.
        value: What the schematic shows for this part, e.g. "10k".
        keywords: Search keywords for KiCad's symbol chooser.
        footprint_filters: Footprint-chooser filter patterns, e.g.
            ["R_0603*"].
        exclude_from_bom: Exclude this part from generated BOMs.
        exclude_from_board: Exclude this part from the PCB.
        exclude_from_sim: Exclude this part from simulation. Defaults
            true; set false only when a SPICE model is configured.

    Returns the resulting configuration, in `get_part_eda`'s shape.
    """
    part = resolve_part(caller, part_id)

    # Only non-default values are passed through, which makes
    # `model_fields_set` mean something. The MCP SDK fills every
    # unsupplied argument with its default before the tool is called, so
    # unlike the PATCH route this surface genuinely cannot tell "omitted"
    # from "sent as the default" — building the model from all twelve
    # arguments would make the audit comment read `fields=<everything>`
    # on every call, which is the same as recording nothing. Recording
    # the non-default fields instead answers the question an auditor
    # actually has: what does this configuration now say?
    fields: dict[str, Any] = {
        "symbol_id": _uuid_arg(symbol_id, "symbol_id"),
        "symbol_ref_external": symbol_ref_external,
        "footprint_id": _uuid_arg(footprint_id, "footprint_id"),
        "footprint_ref_external": footprint_ref_external,
        "spice_datafile_id": _uuid_arg(spice_datafile_id, "spice_datafile_id"),
        "value": value,
        "keywords": keywords,
        "footprint_filters": footprint_filters,
        "exclude_from_bom": exclude_from_bom,
        "exclude_from_board": exclude_from_board,
        "exclude_from_sim": exclude_from_sim,
    }
    supplied = {
        k: v for k, v in fields.items() if v != PartEdaIn.model_fields[k].default
    }
    # Built as the same Pydantic model the REST route takes, so the
    # ref-exclusivity rule, the length limits and the archived-entry
    # check are the service's one implementation and not a second one
    # written in this module.
    payload = PartEdaIn(**supplied)
    config = eda_service.upsert_part_eda(
        caller.db, ws=caller.ws, part=part, user_id=caller.user.id, payload=payload
    )
    audit(
        caller,
        action="part_eda.updated",
        target_type="part_eda",
        target_id=part.id,
        comment="fields=" + ",".join(sorted(payload.model_fields_set)),
    )
    return part_eda_payload(caller, part, config)


def _uuid_arg(value: str | None, field: str) -> UUID | None:
    if value is None or value == "":
        return None
    try:
        return UUID(value)
    except ValueError:
        raise ToolError(f"resource.not_found: {field} is not a valid id") from None


@tool(writes=True, rate=_UPLOAD_RATE)
def upload_eda_asset(
    caller: Caller,
    kind: Literal["symbol", "footprint", "model3d", "spice"],
    filename: str,
    content_base64: str,
    part_id: str | None = None,
    category_slug: str | None = None,
) -> dict[str, Any]:
    """Add a CAD file to the workspace's KiCad library.

    Args:
        kind: What the file is. `symbol` takes a `.kicad_sym` containing
            exactly one symbol (a multi-symbol library is refused — use
            `import_vendor_zip`). `footprint` takes a `.kicad_mod`.
            `model3d` takes a STEP or WRL file. `spice` takes a SPICE
            model.
        filename: The file's name, e.g. "R_0603.kicad_mod". For
            `model3d` the extension decides whether it is read as STEP
            or WRL, so it must be present and correct.
        content_base64: The file's bytes, base64-encoded. Up to 4 MiB
            decoded.
        part_id: Optionally, a part to attach the uploaded file to. Only
            empty slots are filled — an existing symbol or footprint on
            that part is left alone.
        category_slug: Optionally, the category to file the entry under
            in the library.

    Returns the created entry's id and name, and `created: false` when
    an identical file was already in the library under that name (this
    is safe to retry).
    """
    raw = decode_base64(content_base64, what=f"{kind} file")
    category_id = (
        resolve_category(caller, category_slug).id if category_slug else None
    )

    # Same order the HTTP upload uses and for the same reason: validate
    # and digest first, insert the row, write the blob LAST — so a
    # rejected upload cannot leave an orphaned file behind.
    if kind == "symbol":
        entry_name, data = storage.canonical_symbol(raw)
        model, store_kind = EdaSymbol, storage.SYMBOL_KIND
        datafile_kind = None
    elif kind == "footprint":
        entry_name, data = storage.canonical_footprint(raw)
        model, store_kind = EdaFootprint, storage.FOOTPRINT_KIND
        datafile_kind = None
    else:
        datafile_kind = _datafile_kind(kind, filename)
        data = storage.validated_datafile(datafile_kind, raw)
        entry_name = _stem(filename)
        model, store_kind = EdaDatafile, datafile_kind

    sha, size = storage.digest(data)
    row, created = eda_service.upload_entry(
        caller.db,
        ws=caller.ws,
        Model=model,
        user_id=caller.user.id,
        name=entry_name,
        sha256=sha,
        size_bytes=size,
        kind=datafile_kind,
        category_id=category_id,
    )
    storage.store(caller.ws.id, data, kind=store_kind)

    if created:
        entity = _AUDIT_ENTITY[kind]
        audit(
            caller,
            action=f"{entity}.uploaded",
            target_type=entity,
            target_id=row.id,
            # `eda_datafile.uploaded` carries its kind on the REST route
            # because "a datafile was uploaded" is ambiguous between a
            # 3D model and a SPICE deck. Same comment here.
            comment=(
                f"kind={datafile_kind},sha256={sha}"
                if datafile_kind
                else f"sha256={sha}"
            ),
        )

    wired = False
    if part_id:
        wired = _wire_single_entry(caller, part_id, kind=kind, row=row)

    return compact(
        {
            "id": sid(row.id),
            "name": row.name,
            "kind": kind,
            "created": created,
            "sha256": sha,
            "size_bytes": size,
            "part_eda_updated": wired,
        }
    )


# `model3d` is one agent-facing word for two file formats, because the
# agent does not care which and the extension already says. `spice` maps
# straight through.
_AUDIT_ENTITY = {
    "symbol": "eda_symbol",
    "footprint": "eda_footprint",
    "model3d": "eda_datafile",
    "spice": "eda_datafile",
}


def _datafile_kind(kind: str, filename: str) -> str:
    resolved = storage.datafile_kind_from_filename(filename)
    if kind == "spice" and resolved != "spice":
        raise ToolError(
            "eda.unsupported_kind: kind is 'spice' but the filename looks like "
            f"a {resolved} file"
        )
    if kind == "model3d" and resolved not in ("step", "wrl"):
        raise ToolError(
            "eda.unsupported_kind: kind is 'model3d' so the filename must end "
            "in .step, .stp or .wrl"
        )
    return resolved


def _stem(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return base.rsplit(".", 1)[0] or base


def _wire_single_entry(caller: Caller, part_id: str, *, kind: str, row) -> bool:
    """Point a part at one just-uploaded entry, filling empty slots only.

    Reuses `importer.wire_part` — the same function the zip and LCSC
    importers use — by handing it a one-row `ImportResult`. The
    alternative, writing the part_eda row here, would be a second
    implementation of "which slots count as empty", and an external
    `LibNick:Entry` counting as occupied is exactly the kind of rule
    that would drift.
    """
    part = resolve_part(caller, part_id)
    created = importer.CreatedRow(
        id=row.id, name=row.name, created=True, sha256=row.sha256,
        kind=getattr(row, "kind", None),
    )
    result = importer.ImportResult(
        vendor="mcp",
        symbols=[created] if kind == "symbol" else [],
        footprints=[created] if kind == "footprint" else [],
        datafiles=[created] if kind in ("model3d", "spice") else [],
        skipped=[],
    )
    changed = importer.wire_part(
        caller.db,
        ws=caller.ws,
        part=part,
        user_id=caller.user.id,
        result=result,
        overwrite=False,
    )
    if changed:
        audit(
            caller,
            action="part_eda.updated",
            target_type="part_eda",
            target_id=part.id,
            comment=f"fields={kind}",
        )
    return changed


@tool(writes=True, rate=_IMPORT_RATE)
def import_vendor_zip(
    caller: Caller,
    part_id: str,
    content_base64: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Import a vendor CAD archive (SnapEDA, SamacSys, UltraLibrarian) for a part.

    Takes the zip exactly as downloaded from the vendor, works out which
    vendor produced it, and files the symbol, footprint and 3D models it
    contains into this workspace's library — then points the part at
    them.

    Args:
        part_id: The part's id or exact MPN. Its MPN and internal part
            number are used to pick the right entry out of an archive
            that contains several.
        content_base64: The `.zip` file's bytes, base64-encoded. Up to
            4 MiB decoded.
        overwrite: By default only empty slots on the part are filled.
            Set true to replace a symbol or footprint the part already
            has.

    Returns what was imported, `part_eda_updated` for whether the part's
    configuration changed, and `skipped` listing anything in the archive
    that could not be used (KiCad v5 `.lib` symbols, for example).
    """
    raw = decode_base64(content_base64, what="archive")
    part = resolve_part(caller, part_id)
    plan = vendor_zip.read_archive(raw)
    # The same hints the REST route passes, minus the filename — MCP
    # carries no filename for the archive, and the part's own
    # identifiers are the discriminating half anyway.
    plan = vendor_zip.narrow_to_part(
        plan, hints=[part.mpn or "", part.internal_part_number or ""]
    )
    return _import_plan_for_part(caller, part, plan, overwrite=overwrite)


@tool(writes=True, rate=_LCSC_RATE)
def fetch_lcsc(
    caller: Caller,
    part_id: str,
    lcsc_id: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Fetch a part's CAD data from LCSC / EasyEDA and attach it to a part.

    Downloads the symbol, footprint and 3D model EasyEDA publishes for
    an LCSC part number, converts them to KiCad format, files them in
    this workspace's library and points the part at them.

    Args:
        part_id: The part's id or exact MPN.
        lcsc_id: The LCSC part number, e.g. "C25804".
        overwrite: By default only empty slots on the part are filled.
            Set true to replace a symbol or footprint the part already
            has.

    Returns what was imported and whether the part's configuration
    changed. If LCSC has no CAD data for that part number, or is
    unreachable, this fails with `eda.lcsc_not_found` /
    `eda.lcsc_unavailable` — retrying the second is reasonable, the
    first is not.
    """
    part = resolve_part(caller, part_id)
    try:
        plan = lcsc.fetch_plan(lcsc_id)
    except lcsc.LcscNotFound:
        raise ToolError(
            f"eda.lcsc_not_found: LCSC has no CAD data for {lcsc_id!r}"
        ) from None
    except lcsc.LcscUnavailable as exc:
        raise ToolError(f"eda.lcsc_unavailable: {exc}") from None
    return _import_plan_for_part(caller, part, plan, overwrite=overwrite)


def _import_plan_for_part(caller: Caller, part, plan, *, overwrite: bool) -> dict[str, Any]:
    """Run an import plan and record it, exactly as `eda_import.py` does.

    Shared by the zip and LCSC tools because the half after "where did
    the files come from" is identical — and because the audit trail for
    an import must not depend on which door it came through.
    """
    result = importer.import_plan(
        caller.db,
        ws=caller.ws,
        user_id=caller.user.id,
        plan=plan,
        source=plan.vendor,
        category_id=part.category_id,
    )
    result.part_eda_updated = importer.wire_part(
        caller.db,
        ws=caller.ws,
        part=part,
        user_id=caller.user.id,
        result=result,
        overwrite=overwrite,
    )
    # Iterated per BUCKET, not over the flattened `rows`: `kind` is set
    # only on datafiles, so a flat pass cannot tell a symbol from a
    # footprint and would mislabel half the trail.
    for bucket, entity in (
        (result.symbols, "eda_symbol"),
        (result.footprints, "eda_footprint"),
        (result.datafiles, "eda_datafile"),
    ):
        for row in bucket:
            if not row.created:
                continue
            audit(
                caller,
                action=f"{entity}.uploaded",
                target_type=entity,
                target_id=row.id,
                comment=(
                    f"kind={row.kind},sha256={row.sha256}"
                    if row.kind
                    else f"sha256={row.sha256}"
                ),
            )
    audit(
        caller,
        action="part_eda.imported",
        target_type="part_eda",
        target_id=part.id,
        comment=f"vendor={result.vendor},files={len(result.rows)}",
    )
    return compact(
        {
            "part_id": sid(part.id),
            "part_url": part_url(part.id),
            "vendor": result.vendor,
            "created": result.created_count,
            "reused": result.reused_count,
            "part_eda_updated": result.part_eda_updated,
            "symbols": [_row_out(r) for r in result.symbols],
            "footprints": [_row_out(r) for r in result.footprints],
            "datafiles": [_row_out(r) for r in result.datafiles],
            "skipped": [
                compact({"name": s.name, "reason": s.reason}) for s in result.skipped
            ],
        }
    )


def _row_out(row) -> dict[str, Any]:
    return compact(
        {
            "id": sid(row.id),
            "name": row.name,
            "kind": row.kind,
            "created": row.created,
        }
    )
