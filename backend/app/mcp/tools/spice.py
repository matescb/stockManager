"""MCP tools for the structured SPICE binding (SM-009).

`set_spice_binding` is the one writer of a confirmed binding: it checks a
`{symbol_pin: port}` map against the part's symbol and its SPICE model
and derives `part_eda.sim_pins` from it. `list_spice_binding_issues` is
the read-only report over existing parts — it flags, never rewrites.

A new tool rather than new arguments on `set_part_eda`: that tool
replaces the whole configuration from its arguments, and the agents that
call it today would have to learn to pass a binding back on every call.
The logic lives in `domain/eda/spice_binding.py`; this module is the
transport, the audit row and the cursor.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException

from app.core.errors import ErrorCodes
from app.core.pagination import Cursor, decode_cursor, encode_cursor
from app.domain.eda import spice_binding
from app.mcp.principal import Caller
from app.mcp.tools._registry import ToolError, tool
from app.mcp.tools._shared import audit, part_url, resolve_part, sid

_BIND_RATE = "60/minute"  # same ceiling as set_part_eda
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
# Signed into every cursor this tool mints and required on every cursor
# it accepts, so an `export_parts` cursor (same signer, different set)
# cannot be replayed here.
_CURSOR_SCOPE = "mcp.list_spice_binding_issues"


@tool(writes=True, rate=_BIND_RATE)
def set_spice_binding(
    caller: Caller,
    part_id_or_mpn: str,
    pin_map: dict[str, str | None] | None = None,
    model_kind: Literal["file", "native_primitive", "none"] = "file",
    subckt: str | None = None,
    symbol_pins: list[str] | None = None,
    unconnected_ports: list[str] | None = None,
    device_type: str | None = None,
    params: str | None = None,
    fallback: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Record how a part simulates; map symbol pins onto model ports, checked.

    Three kinds. `file` (default) binds the SPICE file attached to the
    part. `native_primitive` records a built-in R, C, L or D simulated
    with `params` and no file (`pin_map` optional: without it the record
    is kept unconfirmed). `none` records that there is no model, with
    the `fallback` to use. The two file-less kinds refuse a part that
    has a file attached and never write the part's `sim_pins`.

    Reads the SPICE model the part points at (attach one first with
    upload_eda_asset kind "spice" + part_id, or set_part_eda), finds the
    `.subckt` (or `.model`) and its ports in order, and checks `pin_map`
    against the symbol and the model. On success it stores the binding
    as validated and writes the part's `sim_pins` (KiCad `Sim.Pins`,
    e.g. "1=IN+ 2=IN- 3=V+ 4=V- 5=OUT"). It changes nothing else on the
    part's CAD configuration. Call get_part_eda first to see the model's
    ports under `spice_binding.ports` when an upload already parsed them.

    Args:
        part_id_or_mpn: The part's id or exact MPN.
        pin_map: Every symbol pin number → a model port name, or null for
            a pin that is not connected in the model. Ports match
            case-insensitively. Every model port must be used exactly
            once or listed in `unconnected_ports`. Native ports: R, C, L
            "+" "-"; D "A" "K".
        model_kind: "file", "native_primitive" or "none".
        subckt: Which `.subckt` / `.model` in the file. Required when the
            file defines more than one.
        symbol_pins: The symbol's pin numbers. Needed only when the
            symbol is not hosted in this workspace (an external
            `LibNick:Entry` or none); a hosted symbol's pins are read
            from its file and this must then match or be omitted.
        unconnected_ports: Model ports no symbol pin drives (an LTspice
            "NC" node, the unused channels of a multi-channel model).
        device_type: native_primitive only: R, C, L or D.
        params: native_primitive only: the primitive's parameters as KiCad
            `Sim.Params` text, e.g. "r=10k" (up to 500 characters).
        fallback: Mainly for none: what to simulate with instead.
        source: Where the record comes from (e.g. a model library
            manifest); for a file, an NG-010 header takes precedence.

    Refusals (nothing is written): eda.spice_model_missing,
    eda.spice_no_model, eda.spice_subckt_ambiguous (lists `available`),
    eda.spice_subckt_unknown, eda.spice_model_not_checkable (a `.model`
    whose device type has no known pin names; D, NPN, PNP, NJF, PJF,
    NMOS, PMOS, VDMOS are known), eda.spice_symbol_pins_unknown,
    eda.spice_symbol_pins_conflict, eda.spice_pin_unknown,
    eda.spice_pin_unmapped, eda.spice_port_unknown,
    eda.spice_port_duplicate, eda.spice_port_count_mismatch,
    eda.spice_sim_pins_too_long, eda.spice_kind_conflict (file-less
    kind on a part with a file), eda.spice_not_applicable (pin_map,
    device_type or params with kind none), eda.spice_field_too_long.

    Returns `spice_binding` (as get_part_eda reports it), `sim_pins` and
    `warnings` (e.g. an NG-010 header whose pin count differs from the
    model line).
    """
    part = resolve_part(caller, part_id_or_mpn)
    result = spice_binding.bind(
        caller.db,
        ws=caller.ws,
        part=part,
        user_id=caller.user.id,
        pin_map=pin_map,
        model_kind=model_kind,
        subckt=subckt,
        symbol_pins=symbol_pins,
        unconnected_ports=unconnected_ports,
        device_type=device_type,
        params=params,
        fallback=fallback,
        source=source,
    )
    binding = result.binding
    audit(
        caller,
        action="part_spice_binding.set",
        target_type="part_spice_binding",
        target_id=part.id,
        comment=(
            f"kind={binding.model_kind},subckt={(binding.subckt or '')[:80]},"
            f"ports={len(binding.ports or [])},sha256={binding.model_sha256 or ''}"
        ),
    )
    return {
        "part_id": sid(part.id),
        "part_url": part_url(part.id),
        "spice_binding": spice_binding.binding_payload(
            caller.db, ws=caller.ws, config=result.config, part_id=part.id
        ),
        "sim_pins": result.config.sim_pins if result.config else None,
        "warnings": result.warnings,
    }


@tool()
def list_spice_binding_issues(
    caller: Caller,
    cursor: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """Parts whose SPICE pin mapping (`Sim.Pins`) is not validated.

    Read-only: lists, never fixes. A part appears when its `sim_pins` is
    free text with no binding (`source: "free-text"`, always issue
    `free_text_unvalidated`, plus what comparing it with the attached
    model found: `port_count_mismatch`, `port_unknown`,
    `sim_pins_unparseable`, `model_ambiguous`, `no_model_in_file`,
    `model_unreadable`, `no_model_attached`), or when it has a binding
    that does not hold (`source: "binding"`: `not_confirmed`,
    `awaiting_reconfirmation`, `model_changed`, `model_detached`,
    `sim_pins_edited`). Fix one with set_spice_binding.

    Args:
        cursor: The `next_cursor` from the previous page. Omit for the
            first page.
        page_size: Parts per page, 1-100 (default 50); larger values are
            reduced to 100.

    Returns `items`, `next_cursor` (null on the last page) and the
    `page_size` used.
    """
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    items, next_after = spice_binding.list_issues(
        caller.db, ws=caller.ws, after=_seek_position(cursor), limit=page_size
    )
    return {
        "items": items,
        "next_cursor": (
            encode_cursor(Cursor(id=next_after, scope=_CURSOR_SCOPE))
            if next_after is not None
            else None
        ),
        "page_size": page_size,
    }


def _seek_position(cursor: str | None) -> UUID | None:
    if cursor is None or cursor == "":
        return None
    try:
        decoded = decode_cursor(cursor)
    except HTTPException:
        decoded = None
    if (
        decoded is None
        or decoded.scope != _CURSOR_SCOPE
        or decoded.sort_key is not None
        or decoded.sort_keys is not None
    ):
        raise ToolError(
            f"{ErrorCodes.MCP_INVALID_CURSOR}: cursor was not issued by "
            "list_spice_binding_issues; call again without a cursor to start over"
        )
    return decoded.id
