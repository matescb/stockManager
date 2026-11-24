"""MCP tool for EDA asset metadata (SM-010).

`set_asset_metadata` writes the descriptive half — origin, model kind,
simulators, validity — for the CURRENT version of one of a part's hosted
CAD files. The review half is not writable here at all: every MCP caller
holds an API token, and a review must come from a person at a browser
(`PUT /api/parts/{id}/eda/{kind}/review`, admin/owner session). The tool
still ACCEPTS `review_state` / `review_against` so that an agent that
tries gets the stable `eda.review_requires_owner` refusal instead of an
"unexpected argument" that reads like a typo.

The read side is `get_part_eda`'s `assets` key, built in
`_shared.part_eda_payload`. Logic lives in `domain/eda/asset_metadata.py`.
"""
from __future__ import annotations

from typing import Any, Literal

from app.core.errors import ErrorCodes
from app.domain.eda import asset_metadata
from app.mcp.principal import Caller
from app.mcp.tools._registry import ToolError, tool
from app.mcp.tools._shared import audit, part_url, resolve_part, sid

_RATE = "60/minute"  # same ceiling as set_part_eda


@tool(writes=True, rate=_RATE)
def set_asset_metadata(
    caller: Caller,
    part_id_or_mpn: str,
    kind: Literal["symbol", "footprint", "model3d", "spice"],
    origin: str | None = None,
    model_kind: Literal["vendor", "converted", "fitted", ""] | None = None,
    simulators: list[str] | None = None,
    valid_for: list[str] | None = None,
    not_valid_for: list[str] | None = None,
    note: str | None = None,
    review_state: str | None = None,
    review_against: str | None = None,
) -> dict[str, Any]:
    """Record where one of a part's CAD files came from and what it is good for.

    Describes the file CURRENTLY in the slot — its exact bytes. Uploading
    a new version of the file starts it over with no metadata and review
    state `unreviewed`. Only the arguments you pass are changed; pass ""
    (or [] for a list) to clear one. Read the current values first with
    get_part_eda, under `assets.<kind>`.

    Args:
        part_id_or_mpn: The part's id or exact MPN.
        kind: Which file: `symbol`, `footprint`, `model3d` (the first 3D
            model on the part's footprint) or `spice`. The slot must hold
            a file hosted here, not a reference to a local KiCad library.
        origin: Where the file came from — a URL or a document reference
            such as "TI SLOS123 rev C, PSpice model".
        model_kind: SPICE only: `vendor` (as supplied by the
            manufacturer), `converted` (translated from another simulator
            dialect) or `fitted` (parameters fitted to the datasheet).
        simulators: Simulators the model was checked with, e.g.
            ["ngspice-43", "LTspice 24"].
        valid_for: Analyses the model is valid for, e.g. ["dc", "ac",
            "transient"].
        not_valid_for: Analyses or conditions it is NOT valid for, e.g.
            ["noise", "temperature"].
        note: Free text, up to 2000 characters.
        review_state: Not settable by a token — a human reviews in the
            app. Passing it (or review_against) is refused with
            eda.review_requires_owner and nothing is written.
        review_against: See review_state.

    Refusals (nothing is written): eda.review_requires_owner,
    eda.asset_not_attached (the slot is empty or external),
    eda.asset_metadata_invalid (bad value, model_kind on a non-SPICE
    file, nothing to set), part.not_found, auth.token_read_only.

    Returns `asset` in get_part_eda's `assets.<kind>` shape and `updated`
    (the fields written).
    """
    if review_state is not None or review_against is not None:
        raise ToolError(
            f"{ErrorCodes.EDA_REVIEW_REQUIRES_OWNER}: review state is set by "
            "a workspace owner or admin in the app, not through an API token; "
            "nothing was written"
        )
    part = resolve_part(caller, part_id_or_mpn)
    provided = {
        name: value
        for name, value in {
            "origin": origin,
            "model_kind": model_kind,
            "simulators": simulators,
            "valid_for": valid_for,
            "not_valid_for": not_valid_for,
            "note": note,
        }.items()
        if value is not None
    }
    ref, updated = asset_metadata.set_metadata(
        caller.db,
        ws=caller.ws,
        part=part,
        kind=kind,
        user_id=caller.user.id,
        fields=provided,
    )
    audit(
        caller,
        action="eda_asset_metadata.set",
        target_type="part_eda",
        target_id=part.id,
        comment=f"kind={kind},sha256={ref.row.sha256},fields={','.join(updated)}",
    )
    return {
        "part_id": sid(part.id),
        "part_url": part_url(part.id),
        "kind": kind,
        "asset": asset_metadata.asset_payload(caller.db, ws=caller.ws, part=part, kind=kind),
        "updated": updated,
    }
