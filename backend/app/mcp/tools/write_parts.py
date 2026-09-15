"""Mutating MCP tools for authoring a part: create it, file it, spec it.

The three tools an assistant reading a schematic or a vendor page needs
before any of the others are useful. They sit apart from
`write_inventory.py` (stock and categories) and `write.py` (the KiCad
library) because they share a different contract: every one of them
takes an identifier a MODEL holds rather than one a database issued.

Two decisions here are worth reading before changing anything.

**A duplicate MPN is an answer, not an error.** `POST /api/parts`
returns 409 with `existing_id` when the MPN is taken, and a human
operator reads that as "oh, it's already there". An agent cannot: a tool
error is indistinguishable from a malformed call, and the only recovery
it can invent is to try again differently. So `create_part` catches that
one conflict and returns `found_existing: true` with the existing part —
the same information, shaped as the success it actually is. Every other
failure stays a failure.

**Provider-owned spec rows are never overwritten.** `set_part_specs`
writes `custom_fields(source='manual')` and skips any row a provider
refresh owns, reporting them back under `skipped_provider_owned`. The
REST route does something different on purpose — a person editing a
provider value in the UI is deliberately taking ownership of it, so it
promotes the row to `source='override'`. An agent doing the same in bulk
is not deliberate about any single row, and a refresh-owned value
silently replaced by a model's guess is the one outcome nobody could
audit afterwards. `_spec_fields.py` holds that rule and the batch caps
that go with it; ADR-0031 owns the namespaces it relies on.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException, status
from pydantic import ValidationError

from app.core.errors import ErrorCodes, raise_http
from app.domain.parts.models import Part
from app.domain.parts.schemas import PartIn
from app.domain.parts.services.create_part import create_part as _create_part
from app.domain.parts.services.mpn_unique import active_part_by_mpn
from app.mcp.principal import Caller
from app.mcp.tools._registry import tool
from app.mcp.tools._shared import (
    audit,
    compact,
    part_summary,
    part_url,
    resolve_category_ref,
    resolve_part,
    sid,
)
from app.mcp.tools._spec_fields import (
    apply_specs,
    keys_comment,
    load_part_fields,
    validate_specs,
)

# `POST /api/parts` and `POST /api/custom-fields` carry no per-route
# slowapi ceiling, so there is no twin number to copy. 60/minute is the
# read default halved, the same reasoning `write_inventory.py` uses for
# stock: these are writes, and an agent needing more than one a second is
# looping on a mistake rather than doing work.
_PART_RATE = "60/minute"


@tool(writes=True, rate=_PART_RATE)
def create_part(
    caller: Caller,
    name: str | None = None,
    mpn: str | None = None,
    manufacturer: str | None = None,
    description: str | None = None,
    category_id: str | None = None,
    part_type: Literal["local", "linked"] | None = None,
    internal_part_number: str | None = None,
) -> dict[str, Any]:
    """Create a part in this workspace, or report the one that exists.

    Args:
        name: What to call it. Optional — defaults to the MPN, so
            pasting a manufacturer part number is enough on its own.
        mpn: The manufacturer part number, e.g. "STM32G071CBT6". Unique
            per workspace.
        manufacturer: Who makes it, e.g. "STMicroelectronics".
        description: One line describing the part.
        category_id: Which category to file it under — a category id,
            its exact name, or its slug, all from `list_categories`.
        part_type: "local" (default) for a part you maintain yourself,
            "linked" for one whose data comes from a parts provider.
        internal_part_number: Your own part number for it, if you use
            one.

    **At least one of `name` and `mpn` is required.**

    When `mpn` is already used by a part here, nothing is created and
    the call SUCCEEDS with `found_existing: true` and that part — an
    MPN is one part, so finding it is the right answer. Check that flag
    before telling the user you added something. Otherwise
    `found_existing` is false and `part` is the new row.

    Stock is not set here; call `add_stock` afterwards.
    """
    fields: dict[str, Any] = {
        "name": name,
        "mpn": mpn,
        "manufacturer": manufacturer,
        "description": description,
        "internal_part_number": internal_part_number,
        "part_type": part_type,
    }
    supplied = {k: v for k, v in fields.items() if v is not None}
    if category_id is not None:
        supplied["category_id"] = resolve_category_ref(caller, category_id).id

    payload = _payload(supplied)
    try:
        part = _create_part(
            caller.db, ws=caller.ws, user_id=caller.user.id, payload=payload
        )
    except HTTPException as exc:
        # STRIPPED, matching what the service pre-checked against. An MPN
        # copied out of a schematic or a BOM cell carries trailing space
        # more often than not, and looking the existing part back up by
        # the raw argument found nothing — so the tool re-raised the
        # conflict as a hard error, which is the single outcome this
        # tool exists to avoid.
        existing = _existing_for_conflict(
            caller, exc, (payload.mpn or "").strip() or None
        )
        if existing is None:
            raise
        return {"found_existing": True, "part": part_summary(existing)}

    audit(
        caller,
        action="part.created",
        target_type="part",
        target_id=part.id,
        comment="fields=" + ",".join(sorted(set(payload.model_fields_set) | {"name"})),
    )
    return {"found_existing": False, "part": part_summary(part)}


def _payload(supplied: dict[str, Any]) -> PartIn:
    """`PartIn` from the tool's arguments, or a refusal naming the field.

    The routes let FastAPI build this model and answer their own 422;
    here it is built by hand, so a `ValidationError` would otherwise
    escape as an unhandled exception and reach the client as
    `Error executing tool create_part` with nothing it could act on.
    The field caps themselves live on the schema next to the column
    widths they mirror.
    """
    try:
        return PartIn(**supplied)
    except ValidationError as exc:
        fields = ", ".join(
            ".".join(str(part) for part in error["loc"]) or "?"
            for error in exc.errors()
        )
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCodes.PART_INVALID_FIELD,
            f"invalid value for {fields}: {exc.error_count()} field(s) "
            "failed validation; check the lengths",
        )
        raise AssertionError("unreachable")  # pragma: no cover


def _existing_for_conflict(
    caller: Caller, exc: HTTPException, mpn: str | None
) -> Part | None:
    """The part already holding this MPN, or None if that is not why we failed.

    Matched on the error CODE rather than the status: 409 is also what an
    archived category raises, and turning that into "found it" would tell
    the agent a part exists when none does.
    """
    detail = exc.detail
    code = detail.get("code") if isinstance(detail, dict) else None
    if code != ErrorCodes.PART_MPN_CONFLICT:
        return None
    return active_part_by_mpn(caller.db, workspace_id=caller.ws.id, mpn=mpn)


@tool(writes=True, rate=_PART_RATE)
def set_part_category(
    caller: Caller,
    part_id_or_mpn: str,
    category_id_or_name: str,
) -> dict[str, Any]:
    """File a part under a category.

    Args:
        part_id_or_mpn: The part's id or its exact MPN.
        category_id_or_name: The category's id, its exact name
            ("Resistors"), or its slug — case-insensitive on the name.
            `list_categories` returns all three.

    Replaces any category the part already had. When the name matches
    nothing, or matches two categories that differ only in case, the
    refusal lists the categories that exist so you can pick one; pass an
    id to settle a tie. Create a missing category with
    `create_category`.
    """
    part = resolve_part(caller, part_id_or_mpn)
    category = resolve_category_ref(caller, category_id_or_name)

    part.category_id = category.id
    part.updated_by = caller.user.id
    caller.db.flush()

    audit(
        caller,
        action="part.updated",
        target_type="part",
        target_id=part.id,
        comment="fields=category_id",
    )
    return {
        "part": part_summary(part),
        "category": compact(
            {
                "id": sid(category.id),
                "name": category.name,
                "slug": category.library_slug,
            }
        ),
    }


@tool(writes=True, rate=_PART_RATE)
def set_part_specs(
    caller: Caller,
    part_id_or_mpn: str,
    specs: dict[str, str],
    replace_missing: bool = False,
) -> dict[str, Any]:
    """Write a part's specifications — resistance, tolerance, package, ….

    Args:
        part_id_or_mpn: The part's id or its exact MPN.
        specs: Field name to value, e.g.
            `{"resistance": "10k", "tolerance": "1%", "package": "0402"}`.
            Values are stored verbatim, units included. At most 50 keys,
            256 characters per key and 1024 per value.
        replace_missing: When true, specifications YOU wrote earlier and
            did not repeat here are deleted. Only plain manual rows go:
            provider-supplied values stay, and so does any value a
            person edited by hand in the app, which you can update but
            not remove. Defaults to false, which only adds and updates.

    Specifications supplied by a parts provider are NEVER changed, and
    come back under `skipped_provider_owned` so you can see which values
    the tool refused to touch; `replace_missing` leaves them alone too.
    To change one of those, edit it in the app, which records that a
    person took ownership of it.

    Keys must not have leading or trailing whitespace — `"Tolerance "`
    is refused rather than stored as a second field beside
    `"Tolerance"`.

    Keys a provider reserves (`image_url`, `datasheet_url`,
    `source_url`, and anything prefixed `digikey:` or `mouser:`) are
    refused outright — the whole call, so a batch never lands half
    written.

    Returns which keys were `created`, `updated`, left `unchanged`,
    `skipped_provider_owned`, and (with `replace_missing`) `removed`.
    """
    part = resolve_part(caller, part_id_or_mpn)
    validate_specs(specs)

    existing = load_part_fields(caller, part.id)
    written = apply_specs(caller, part.id, specs, existing, replace_missing=replace_missing)
    caller.db.flush()

    if written.touched:
        audit(
            caller,
            action="part.specs_updated",
            target_type="part",
            target_id=part.id,
            comment=keys_comment(written.touched),
        )

    return compact(
        {
            "part_id": sid(part.id),
            "part_url": part_url(part.id),
            **written.as_payload(),
        }
    )
