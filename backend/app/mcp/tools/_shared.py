"""Lookup and serialisation shared by the MCP tools.

Two jobs.

**Resolution.** Turning the loose identifiers an agent will actually
send — a UUID string, an MPN, a category slug — into a row in *this*
workspace. Every function here filters by `caller.ws.id` and raises the
app's own 404 through `raise_http`, which `_registry` renders as a tool
error. That is the workspace-isolation invariant restated for a surface
with no route to hang `assert_in_workspace` off: an id from another
tenant is not found, never forbidden.

**Serialisation.** Tool results are JSON documents a language model
reads, which makes them a different problem from the API's envelope.
Three rules follow, and they are why these payloads are hand-built
rather than reusing `_parts_shared.serialize_part`:

* every id is a string, so a model never has to reason about a UUID
  object round-tripping;
* every part carries `part_url`, because "show me the part" is the
  single most common thing a user asks next and the model cannot
  construct that URL on its own;
* nulls and empty collections are dropped. A model pays attention to
  every key it is shown, and forty nulls per part is forty chances to
  hallucinate significance in an absent field.
"""
from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from fastapi import status
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import ErrorCodes, raise_http
from app.domain.audit.service import log as _audit_log
from app.domain.categories.models import PartCategory
from app.domain.custom_fields.models import CustomField
from app.domain.eda.models import (
    EdaDatafile,
    EdaFootprint,
    EdaFootprintModel,
    EdaSymbol,
    PartEda,
)
from app.domain.parts.models import Part
from app.domain.parts.provider_fields import is_provider_reserved_custom_field_key
from app.domain.storage.models import StorageLocation
from app.mcp.principal import Caller

# Decoded-payload ceiling for every base64 tool argument. Matches the
# BOM-import lane's cap rather than `MAX_UPLOAD_BYTES`: base64 inflates
# by 4/3 and the JSON-RPC body carries it as one string, so the wire cost
# of the largest accepted payload is ~5.4 MiB. `app/mcp/server.py` sizes
# the transport's own body limit to match; the two must move together.
MAX_DECODED_BYTES = 4 * 1024 * 1024


def sid(value: Any) -> str | None:
    """`str` for ids and enums, `None` passed through."""
    return None if value is None else str(value)


def compact(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None or an empty list/dict/string.

    `False` and `0` are kept — they are answers, not absences.
    """
    return {
        k: v
        for k, v in payload.items()
        if v is not None and v != [] and v != {} and v != ""
    }


def part_url(part_id: UUID) -> str:
    """The app URL a human would open for this part.

    Same construction as the KiCad HTTP library's `stockManager` field
    (`domain/eda/kicad_library.py:471`) so the two surfaces can't
    disagree about where a part lives.
    """
    return f"{settings().APP_BASE_URL.rstrip('/')}/parts/{part_id}"


def audit(
    caller: Caller,
    *,
    action: str,
    target_type: str,
    target_id: UUID,
    comment: str | None = None,
) -> None:
    """Write the audit row for one MCP mutation.

    The MCP twin of `api/routes/_eda_shared.py::audit`. It exists
    separately only because there is no `Request` here to read the
    request id off — the id comes from the principal, stamped by the
    ASGI wrapper. Everything else is deliberately identical, including
    attributing the row to the token's OWNER: an agent is not a
    principal in this system, it is a person's credential acting on
    their behalf, and the audit trail has to name someone who can be
    asked about it.
    """
    _audit_log(
        caller.db,
        ws=caller.ws,
        user=caller.user,
        action=action,
        target_type=target_type,
        target_ids=[target_id],
        comment=comment,
        request_id=caller.principal.request_id,
    )


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def _as_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        return None


def resolve_part(caller: Caller, id_or_mpn: str, *, include_archived: bool = False) -> Part:
    """A part in this workspace by UUID or by exact MPN.

    Accepting both is the point: an agent reading a schematic has an
    MPN, an agent following up on an earlier tool result has an id, and
    forcing it to pick the right tool for which one it holds is a step
    that only ever produces mistakes. A value that parses as a UUID is
    tried as an id first and NOT then retried as an MPN — an MPN shaped
    like a UUID would be a coincidence, and silently falling through
    would make "not found" mean two different things.

    MPN matching is exact and case-sensitive, the same rule
    `uq_parts_ws_mpn` enforces, so this can never return two parts.
    """
    stmt = select(Part).where(Part.workspace_id == caller.ws.id)
    if not include_archived:
        stmt = stmt.where(Part.archived_at.is_(None))

    parsed = _as_uuid(id_or_mpn)
    if parsed is not None:
        stmt = stmt.where(Part.id == parsed)
    else:
        stmt = stmt.where(Part.mpn == id_or_mpn)

    row = caller.db.execute(stmt).scalars().first()
    if row is None:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.PART_NOT_FOUND,
            f"no part in this workspace matching {id_or_mpn!r}",
        )
    return row


def resolve_category(caller: Caller, slug: str) -> PartCategory:
    """A category by its library slug (`library_slug`), scoped to the workspace.

    The agent-facing argument is called `category_slug` because that is
    what it reads like in a tool call; the column is `library_slug`
    because on the KiCad side it names a symbol library. Same value.
    """
    row = (
        caller.db.execute(
            select(PartCategory)
            .where(PartCategory.workspace_id == caller.ws.id)
            .where(PartCategory.library_slug == slug)
            .where(PartCategory.archived_at.is_(None))
        )
        .scalars()
        .first()
    )
    if row is None:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.CATEGORY_NOT_FOUND,
            f"no active category with slug {slug!r} in this workspace",
        )
    return row


def resolve_storage(caller: Caller, storage_id: str) -> StorageLocation:
    parsed = _as_uuid(storage_id)
    row = (
        None
        if parsed is None
        else caller.db.get(StorageLocation, parsed)
    )
    if row is None or row.workspace_id != caller.ws.id:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.RESOURCE_NOT_FOUND,
            f"no storage location {storage_id!r} in this workspace",
        )
    return row


def decode_base64(content_base64: str, *, what: str) -> bytes:
    """Decode a base64 tool argument, or refuse it with a usable reason.

    Capped BEFORE decoding as well as after: the encoded string is
    already in memory by the time a tool runs, but refusing on its
    length keeps a malformed 100 MiB argument from being expanded into
    bytes as well.
    """
    # 4/3 expansion, plus padding. Anything over this cannot decode to
    # something within the cap.
    if len(content_base64) > (MAX_DECODED_BYTES // 3) * 4 + 4:
        raise_http(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            ErrorCodes.EDA_FILE_TOO_LARGE,
            f"{what} exceeds the {MAX_DECODED_BYTES // (1024 * 1024)} MiB limit",
        )
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCodes.EDA_INVALID_FILE,
            f"{what} is not valid base64",
        )
    if not raw:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCodes.EDA_EMPTY_FILE,
            f"{what} decoded to zero bytes",
        )
    if len(raw) > MAX_DECODED_BYTES:
        raise_http(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            ErrorCodes.EDA_FILE_TOO_LARGE,
            f"{what} exceeds the {MAX_DECODED_BYTES // (1024 * 1024)} MiB limit",
        )
    return raw


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def part_summary(part: Part) -> dict[str, Any]:
    """The shape every listing returns. Enough to choose a part, no more."""
    return compact(
        {
            "id": sid(part.id),
            "name": part.name,
            "mpn": part.mpn,
            "manufacturer": part.manufacturer,
            "internal_part_number": part.internal_part_number,
            "description": part.description,
            "category_id": sid(part.category_id),
            "part_url": part_url(part.id),
        }
    )


def custom_fields_for(caller: Caller, part: Part) -> tuple[dict, dict]:
    """This part's custom fields, split into catalog metadata and specs.

    The boundary is `domain/parts/provider_fields.py` — the same list
    `api/routes/parts_assets.py` guards and the frontend's
    `lib/providerCatalog.ts` mirrors. Keeping the split here rather than
    handing an agent one flat bag matters: `image_url` is plumbing, and
    a model that cannot tell it from `Tolerance` will happily quote it
    as a part specification.
    """
    rows = (
        caller.db.execute(
            select(CustomField)
            .where(CustomField.workspace_id == caller.ws.id)
            .where(CustomField.object_type == "part")
            .where(CustomField.object_id == part.id)
            .order_by(CustomField.key)
        )
        .scalars()
        .all()
    )
    catalog: dict[str, str] = {}
    specs: dict[str, str] = {}
    for row in rows:
        target = catalog if is_provider_reserved_custom_field_key(row.key) else specs
        target[row.key] = row.value
    return catalog, specs


_NO_EDA: dict[str, bool] = {
    "has_symbol": False,
    "has_footprint": False,
    "has_model3d": False,
    "has_spice": False,
}


def eda_status_for_parts(
    caller: Caller, part_ids: Sequence[UUID]
) -> dict[UUID, dict[str, Any]]:
    """CAD status for many parts, in two queries regardless of count.

    `find_parts_missing_eda` asks this for every part in the workspace,
    so the per-part version below was two queries per row — the N+1 that
    made a 2,000-part library 4,000 round trips to answer one question.

    Two queries, not one: the `part_eda` rows come first, and only then
    do we know which footprints to ask about 3D models for. A join would
    fold them into one at the cost of a row multiplier on parts sharing
    a footprint, which is the common case in a real library.
    """
    if not part_ids:
        return {}

    configs = list(
        caller.db.execute(
            select(PartEda)
            .where(PartEda.workspace_id == caller.ws.id)
            .where(PartEda.part_id.in_(list(part_ids)))
        ).scalars()
    )

    # A 3D model hangs off the FOOTPRINT, not the part — so "does this
    # part have a 3D model" is a question about the footprint it is
    # configured with. A part with an external footprint ref therefore
    # reports False even if the user has a model locally: we don't host
    # it and can't see it.
    footprint_ids = {c.footprint_id for c in configs if c.footprint_id is not None}
    modelled: set[UUID] = set()
    if footprint_ids:
        modelled = {
            row[0]
            for row in caller.db.execute(
                select(EdaFootprintModel.footprint_id)
                .where(EdaFootprintModel.workspace_id == caller.ws.id)
                .where(EdaFootprintModel.footprint_id.in_(list(footprint_ids)))
                .distinct()
            ).all()
        }

    status = {part_id: dict(_NO_EDA) for part_id in part_ids}
    for config in configs:
        status[config.part_id] = {
            "has_symbol": bool(config.symbol_id or config.symbol_ref_external),
            "has_footprint": bool(
                config.footprint_id or config.footprint_ref_external
            ),
            "has_model3d": config.footprint_id in modelled,
            "has_spice": config.spice_datafile_id is not None,
        }
    return status


def eda_status(caller: Caller, part: Part) -> dict[str, Any]:
    """What CAD data one part has, in the terms an agent asks about.

    Deliberately answers "is there a symbol?" rather than "which symbol
    row?": the caller's next question is almost always whether the part
    is usable in a schematic yet, and a hosted row and an external
    `Device:R` reference both mean yes.

    A thin wrapper over the batched version so that the two can never
    disagree about what "missing a footprint" means — `get_part` and
    `find_parts_missing_eda` have to give the same answer for the same
    part or the second one sends the agent to fix something the first
    says is fine.
    """
    return eda_status_for_parts(caller, [part.id])[part.id]


def _entry_name(caller: Caller, model: type, entry_id: UUID | None) -> str | None:
    if entry_id is None:
        return None
    row = caller.db.get(model, entry_id)
    return None if row is None else row.name


def part_eda_payload(caller: Caller, part: Part, config: PartEda | None) -> dict[str, Any]:
    """The full EDA configuration for one part, with names resolved.

    Hosted references are reported as both the row id and the KiCad
    `PCM_SM_<slug>:<Entry>` string the user's schematic will actually
    contain, because those are two different questions ("what do I edit"
    vs "what will KiCad show") and an agent maintaining a library needs
    both. The refs are built by `domain/eda/kicad_refs.py`, which is the
    same module the PCM package generation uses — so a ref reported here
    is the ref that ships.
    """
    from app.domain.eda import kicad_refs

    if config is None:
        return {"part_id": sid(part.id), "part_url": part_url(part.id), "configured": False}

    category = (
        caller.db.get(PartCategory, part.category_id)
        if part.category_id is not None
        else None
    )
    slug = category.library_slug if category is not None else None

    symbol_name = _entry_name(caller, EdaSymbol, config.symbol_id)
    footprint_name = _entry_name(caller, EdaFootprint, config.footprint_id)
    spice_name = _entry_name(caller, EdaDatafile, config.spice_datafile_id)

    return compact(
        {
            "part_id": sid(part.id),
            "part_url": part_url(part.id),
            "configured": True,
            "symbol_id": sid(config.symbol_id),
            "symbol_name": symbol_name,
            "symbol_ref": (
                kicad_refs.entry_ref(symbol_name, slug) if symbol_name else None
            ),
            "symbol_ref_external": config.symbol_ref_external,
            "footprint_id": sid(config.footprint_id),
            "footprint_name": footprint_name,
            "footprint_ref": (
                kicad_refs.entry_ref(footprint_name, slug) if footprint_name else None
            ),
            "footprint_ref_external": config.footprint_ref_external,
            "spice_datafile_id": sid(config.spice_datafile_id),
            "spice_name": spice_name,
            "value": config.value,
            "keywords": config.keywords,
            "footprint_filters": list(config.footprint_filters or []),
            "exclude_from_bom": config.exclude_from_bom,
            "exclude_from_board": config.exclude_from_board,
            "exclude_from_sim": config.exclude_from_sim,
            "sim_device": config.sim_device,
            "sim_pins": config.sim_pins,
            "sim_params": config.sim_params,
        }
    )
