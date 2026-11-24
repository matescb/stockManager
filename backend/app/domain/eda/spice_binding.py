"""Structured SPICE binding for one part (SM-009).

`part_eda.sim_pins` is the string the KiCad HTTP library emits as
`Sim.Pins` ("1=IN+ 2=IN- 3=V+ …"). Until SM-009 it was free text nothing
checked. This module stores, beside it, the model's subcircuit name and
ordered ports (parsed by `spice_model.py`) and a `{symbol_pin: port}`
map checked against the symbol, and DERIVES `sim_pins` from that map.
`sim_pins` stays the emitted carrier; no emitted field name changes.

Three entry points, one per caller:

* `bind` — the `set_spice_binding` MCP tool. Validates and writes.
* `record_upload` — `upload_eda_asset` with a `part_id`. Stores what the
  file says (subckt, ports, NG-010 header) WITHOUT a confirmed mapping,
  and marks an existing binding unvalidated when the bytes changed. It
  never deletes a binding or touches `sim_pins`.
* `binding_payload` / `list_issues` — the read side. Whether a binding
  still holds is computed here, at read time, from the live `part_eda`
  row: a different file, different bytes, or a hand-edited `sim_pins`
  make it unvalidated whichever path caused the change (`set_part_eda`,
  the REST PUT, a vendor-zip import) — none of them has to know.

Workspace isolation: every query filters on `ws.id`; the datafile and
symbol rows are only ever reached through the part's own `part_eda` row,
which is itself workspace-filtered.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.errors import ErrorCodes, raise_http
from app.core.time import utcnow
from app.domain.eda import pin_pad, sexpr, spice_model, storage
from app.domain.eda.models import EdaDatafile, EdaSymbol, PartEda, PartSpiceBinding
from app.domain.parts.models import Part

# `part_eda.sim_pins` is String(300).
_SIM_PINS_MAX = 300
# Spelling of "not connected" accepted as a pin_map value besides null,
# unless the model really has a port of that name.
_NC = "nc"
_UNREADABLE = "model_unreadable"
_FILELESS_KINDS = ("native_primitive", "none")


def _unprocessable(code: str, message: str, **fields: Any):
    raise_http(status.HTTP_422_UNPROCESSABLE_CONTENT, code=code, message=message, **fields)


def _natural_key(pin: str) -> tuple:
    return tuple(
        (0, int(p), "") if p.isdigit() else (1, 0, p)
        for p in re.split(r"(\d+)", pin)
        if p
    )


def sorted_pins(pins: Sequence[str]) -> list[str]:
    return sorted(pins, key=_natural_key)


# ---------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------


def read_model_text(datafile: EdaDatafile) -> str | None:
    """The stored SPICE file's text, or None when the blob is missing."""
    path = storage.path_for(
        datafile.workspace_id, f"{datafile.sha256}.{storage.EXT_BY_KIND['spice']}"
    )
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _symbol_pin_numbers(node) -> list[str]:
    """Pin numbers of a `(symbol …)` node, through SM-008's one reader.

    Body style 1 only (style 0 is common to all), as `pin_pad` checks it;
    a De Morgan alternate repeats the same numbers. Multi-unit symbols
    repeat shared pins; the first occurrence wins. A derived (`extends`)
    symbol has no pins of its own and reads as unknown.
    """
    try:
        pins = pin_pad.symbol_pins(node)
    except pin_pad.NotCheckable:
        return []
    found: list[str] = []
    for pin in pins:
        if pin.style in (0, 1) and pin.number not in found:
            found.append(pin.number)
    return found


def hosted_symbol_pins(db: Session, *, ws: Any, config: PartEda) -> list[str] | None:
    """Pin numbers of the part's hosted symbol, or None if it has none."""
    if config.symbol_id is None:
        return None
    symbol = db.execute(
        select(EdaSymbol)
        .where(EdaSymbol.workspace_id == ws.id)
        .where(EdaSymbol.id == config.symbol_id)
    ).scalars().first()
    if symbol is None:
        return None
    path = storage.path_for(
        ws.id, f"{symbol.sha256}.{storage.EXT_BY_KIND[storage.SYMBOL_KIND]}"
    )
    try:
        with open(path, encoding="utf-8") as fh:
            found = sexpr.entries(fh.read())
    except (OSError, sexpr.SexprError):
        return None
    return _symbol_pin_numbers(found[0][1]) if found else None


def _config(db: Session, *, ws: Any, part_id: UUID) -> PartEda | None:
    return db.execute(
        select(PartEda)
        .where(PartEda.workspace_id == ws.id)
        .where(PartEda.part_id == part_id)
    ).scalars().first()


def _binding(db: Session, *, ws: Any, part_id: UUID) -> PartSpiceBinding | None:
    return db.execute(
        select(PartSpiceBinding)
        .where(PartSpiceBinding.workspace_id == ws.id)
        .where(PartSpiceBinding.part_id == part_id)
    ).scalars().first()


def _datafile(db: Session, *, ws: Any, datafile_id: UUID | None) -> EdaDatafile | None:
    if datafile_id is None:
        return None
    return db.execute(
        select(EdaDatafile)
        .where(EdaDatafile.workspace_id == ws.id)
        .where(EdaDatafile.id == datafile_id)
    ).scalars().first()


# ---------------------------------------------------------------------
# Write: confirm a mapping
# ---------------------------------------------------------------------


@dataclass
class BindResult:
    binding: PartSpiceBinding
    config: PartEda | None
    datafile: EdaDatafile | None
    warnings: list[str]


def _choose_model(models: list[spice_model.SpiceModel], subckt: str | None):
    if subckt:
        chosen = spice_model.find_model(models, subckt)
        if chosen is None:
            _unprocessable(
                ErrorCodes.EDA_SPICE_SUBCKT_UNKNOWN,
                f"the model file defines no .subckt or .model named {subckt[:80]!r}",
                available=[m.name for m in models[:50]],
            )
        return chosen
    if not models:
        _unprocessable(
            ErrorCodes.EDA_SPICE_NO_MODEL,
            "the model file defines no top-level .subckt or .model",
        )
    if len(models) > 1:
        _unprocessable(
            ErrorCodes.EDA_SPICE_SUBCKT_AMBIGUOUS,
            f"the model file defines {len(models)} models; pass subckt to choose one",
            available=[m.name for m in models[:50]],
        )
    return models[0]


def _symbol_pins(
    db: Session, *, ws: Any, config: PartEda | None, declared: Sequence[str] | None
) -> tuple[list[str], str]:
    hosted = hosted_symbol_pins(db, ws=ws, config=config) if config else None
    if hosted:
        if declared and set(map(str, declared)) != set(hosted):
            _unprocessable(
                ErrorCodes.EDA_SPICE_SYMBOL_PINS_CONFLICT,
                "symbol_pins does not match the pins of the part's hosted symbol",
                symbol_pins=sorted_pins(hosted),
            )
        return sorted_pins(hosted), "hosted_symbol"
    if declared:
        pins = [str(p).strip() for p in declared]
        if any(not p or " " in p or "=" in p for p in pins) or len(set(pins)) != len(pins):
            _unprocessable(
                ErrorCodes.EDA_SPICE_SYMBOL_PINS_UNKNOWN,
                "symbol_pins must be distinct pin numbers without spaces or '='",
            )
        return sorted_pins(pins), "declared"
    _unprocessable(
        ErrorCodes.EDA_SPICE_SYMBOL_PINS_UNKNOWN,
        "the part's symbol is not hosted in this workspace, so its pins cannot be "
        "read; pass symbol_pins with the symbol's pin numbers",
    )
    raise AssertionError("unreachable")


def check_pin_map(
    pin_map: Mapping[str, str | None],
    *,
    symbol_pins: Sequence[str],
    ports: Sequence[str],
    unconnected: Sequence[str] = (),
) -> list[tuple[str, str]]:
    """Validate a mapping; return the `(symbol_pin, port)` pairs to emit.

    Ports are matched case-insensitively (SPICE node names are) and
    emitted in the model's own spelling. A pin mapped to null — or to
    "NC" when the model has no port of that name — is not connected and
    is left out of `Sim.Pins`. A model port no symbol pin drives (an
    LTspice "NC" node, one channel of a quad part) must be named in
    `unconnected`; otherwise every port must be mapped exactly once.
    """
    mapping = {str(k): v for k, v in pin_map.items()}
    unknown_pins = [p for p in mapping if p not in symbol_pins]
    if unknown_pins:
        _unprocessable(
            ErrorCodes.EDA_SPICE_PIN_UNKNOWN,
            f"pin_map names pins the symbol does not have: {', '.join(unknown_pins[:20])}",
            symbol_pins=list(symbol_pins),
        )
    unmapped = [p for p in symbol_pins if p not in mapping]
    if unmapped:
        _unprocessable(
            ErrorCodes.EDA_SPICE_PIN_UNMAPPED,
            f"symbol pins not in pin_map: {', '.join(unmapped[:20])} — map each to a "
            "port, or to null for not connected",
            unmapped=unmapped,
        )
    by_lower = {p.lower(): p for p in ports}
    pairs: list[tuple[str, str]] = []
    missing: list[str] = []
    for pin in symbol_pins:
        target = mapping[pin]
        if target is None:
            continue
        target = str(target).strip()
        if target.lower() == _NC and _NC not in by_lower:
            continue
        port = by_lower.get(target.lower())
        if port is None:
            missing.append(target)
            continue
        pairs.append((pin, port))
    if missing:
        _unprocessable(
            ErrorCodes.EDA_SPICE_PORT_UNKNOWN,
            f"pin_map names ports the model does not have: {', '.join(missing[:20])}",
            ports=list(ports),
        )
    floating: list[str] = []
    for name in unconnected:
        port = by_lower.get(str(name).strip().lower())
        if port is None:
            missing.append(str(name))
        else:
            floating.append(port)
    if missing:
        _unprocessable(
            ErrorCodes.EDA_SPICE_PORT_UNKNOWN,
            f"unconnected_ports names ports the model does not have: {', '.join(missing[:20])}",
            ports=list(ports),
        )
    used = [port for _, port in pairs] + floating
    dupes = sorted({p for p in used if used.count(p) > 1})
    if dupes:
        _unprocessable(
            ErrorCodes.EDA_SPICE_PORT_DUPLICATE,
            f"port(s) mapped more than once or both mapped and unconnected: "
            f"{', '.join(dupes)} — map ONE symbol pin per port and the others on "
            "the same net (a second VIN pin, an exposed pad) to null",
        )
    if len(used) != len(ports):
        left = [p for p in ports if p not in used]
        _unprocessable(
            ErrorCodes.EDA_SPICE_PORT_COUNT_MISMATCH,
            f"the model has {len(ports)} ports and pin_map connects {len(pairs)}; "
            f"unbound: {', '.join(left[:20])} — map them, or list them in "
            "unconnected_ports",
            ports=list(ports),
            unbound=left,
        )
    return pairs


def _apply_header(binding: PartSpiceBinding, text: str) -> spice_model.Ng010Header | None:
    """Copy NG-010 header metadata onto the binding; None when absent.

    The columns describe the file at `model_sha256`, so a file without a
    header clears them rather than inheriting the previous file's.
    """
    header = spice_model.parse_ng010_header(text)
    if header is None:
        binding.model_source = binding.build_requirement = binding.grade = None
        binding.header_pins = None
        return None
    binding.model_source = spice_model.model_source_text(header)[:500] or None
    binding.build_requirement = (header.build_requirement or "")[:500] or None
    binding.grade = (header.grade or "")[:500] or None
    binding.header_pins = list(header.pins) or None
    return header


def _set_model(
    binding: PartSpiceBinding, model: spice_model.SpiceModel, datafile: EdaDatafile
) -> None:
    binding.subckt = model.name[:200]
    binding.model_kind = model.kind
    binding.device_type = model.device_type
    binding.ports = list(model.ports)
    binding.model_sha256 = datafile.sha256
    binding.spice_datafile_id = datafile.id
    binding.params = None
    binding.fallback = None


MODEL_KINDS = ("file", "native_primitive", "none")
_TEXT_MAX = 500


def bind(
    db: Session,
    *,
    ws: Any,
    part: Part,
    user_id: UUID | None,
    pin_map: Mapping[str, str | None] | None,
    model_kind: str = "file",
    subckt: str | None = None,
    symbol_pins: Sequence[str] | None = None,
    unconnected_ports: Sequence[str] | None = None,
    device_type: str | None = None,
    params: str | None = None,
    fallback: str | None = None,
    source: str | None = None,
) -> BindResult:
    """Record how a part simulates; validate and store it.

    `file` binds the attached SPICE file (see `_bind_file`).
    `native_primitive` records a built-in R / C / L / D with parameters
    and no file; `none` records that there is no model, with the
    fallback to use instead. Neither of those touches `part_eda` — the
    KiCad HTTP library emits `Sim.*` only for a part with a SPICE file,
    so a `sim_pins` written there would be emitted by nothing.
    Every refusal is a 422 with a stable code and changes nothing.
    """
    for label, value in (("params", params), ("fallback", fallback), ("source", source)):
        if value is not None and len(value) > _TEXT_MAX:
            _unprocessable(
                ErrorCodes.EDA_SPICE_FIELD_TOO_LONG,
                f"{label} is {len(value)} characters; the limit is {_TEXT_MAX}",
            )
    if model_kind == "file":
        return _bind_file(
            db, ws=ws, part=part, user_id=user_id, pin_map=pin_map or {},
            subckt=subckt, symbol_pins=symbol_pins,
            unconnected_ports=list(unconnected_ports or []), source=source,
        )
    config = _config(db, ws=ws, part_id=part.id)
    if config is not None and config.spice_datafile_id is not None:
        _unprocessable(
            ErrorCodes.EDA_SPICE_KIND_CONFLICT,
            f"the part has a SPICE file attached; model_kind {model_kind!r} records a "
            "part WITHOUT one — bind the file with model_kind 'file', or detach it first",
        )
    if model_kind == "native_primitive":
        ports, pairs, pins_source = _native_ports_and_pairs(
            db, ws=ws, config=config, device_type=device_type, pin_map=pin_map,
            symbol_pins=symbol_pins,
        )
    else:
        if pin_map or device_type or params:
            _unprocessable(
                ErrorCodes.EDA_SPICE_NOT_APPLICABLE,
                "model_kind 'none' takes no pin_map, device_type or params — only "
                "fallback and source",
            )
        ports, pairs, pins_source = (), None, None
    binding = _upsert_binding(db, ws=ws, part=part, user_id=user_id)
    binding.model_kind = model_kind
    binding.subckt = None
    binding.device_type = device_type.upper() if device_type else None
    binding.ports = list(ports)
    binding.params = params or None
    binding.fallback = fallback or None
    binding.model_source = source or None
    binding.spice_datafile_id = binding.model_sha256 = None
    binding.build_requirement = binding.grade = binding.header_pins = None
    binding.unconnected_ports = None
    binding.symbol_pins_source = pins_source
    if pairs is None:
        binding.pin_map = binding.sim_pins_derived = None
        binding.validated, binding.validated_at = False, None
    else:
        connected = dict(pairs)
        binding.pin_map = {pin: connected.get(pin) for pin in pin_map or {}}
        binding.sim_pins_derived = spice_model.format_sim_pins(pairs)
        binding.validated, binding.validated_at = True, utcnow()
    binding.updated_by = user_id
    db.flush()
    return BindResult(binding=binding, config=config, datafile=None, warnings=[])


def _native_ports_and_pairs(
    db: Session, *, ws: Any, config: PartEda | None, device_type: str | None,
    pin_map: Mapping[str, str | None] | None, symbol_pins: Sequence[str] | None,
):
    ports = spice_model.NATIVE_PORTS.get((device_type or "").upper())
    if ports is None:
        _unprocessable(
            ErrorCodes.EDA_SPICE_NOT_CHECKABLE,
            f"device_type {str(device_type)[:20]!r} is not a known native primitive; "
            f"use one of {', '.join(spice_model.NATIVE_PORTS)}",
        )
    if not pin_map:
        # Recorded, not confirmed: the kind and parameters are still worth
        # having when nobody has checked which pin is which.
        return ports, None, None
    pins, pins_source = _symbol_pins(db, ws=ws, config=config, declared=symbol_pins)
    return ports, check_pin_map(pin_map, symbol_pins=pins, ports=ports), pins_source


def _upsert_binding(db: Session, *, ws: Any, part: Part, user_id: UUID | None):
    binding = _binding(db, ws=ws, part_id=part.id)
    if binding is None:
        binding = PartSpiceBinding(workspace_id=ws.id, part_id=part.id, created_by=user_id)
        db.add(binding)
    return binding


def _ports_in_order(ports: Sequence[str], names: Sequence[str]) -> list[str] | None:
    wanted = {str(n).strip().lower() for n in names}
    return [p for p in ports if p.lower() in wanted] or None


def _bind_file(
    db: Session,
    *,
    ws: Any,
    part: Part,
    user_id: UUID | None,
    pin_map: Mapping[str, str | None],
    subckt: str | None,
    symbol_pins: Sequence[str] | None,
    unconnected_ports: Sequence[str],
    source: str | None,
) -> BindResult:
    """A file-backed binding: the part's attached `.subckt` / `.model`.

    On success the binding is `validated`, and `part_eda.sim_pins` is
    overwritten with the derived string — the one column of `part_eda`
    this touches.
    """
    config = _config(db, ws=ws, part_id=part.id)
    datafile = _datafile(db, ws=ws, datafile_id=config.spice_datafile_id if config else None)
    if config is None or datafile is None:
        _unprocessable(
            ErrorCodes.EDA_SPICE_MODEL_MISSING,
            "the part has no SPICE model attached; upload one with upload_eda_asset "
            "(kind spice, part_id) or set spice_datafile_id with set_part_eda first",
        )
    text = read_model_text(datafile)
    if text is None:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.EDA_FILE_NOT_FOUND,
            message="the SPICE model's stored file is missing",
        )
    model = _choose_model(spice_model.parse_models(text), subckt)
    if not model.checkable:
        _unprocessable(
            ErrorCodes.EDA_SPICE_NOT_CHECKABLE,
            f".model {model.name[:80]} is a {model.device_type} card; ports are known "
            f"only for {', '.join(sorted(spice_model.PRIMITIVE_PORTS))}",
        )
    pins, pins_source = _symbol_pins(db, ws=ws, config=config, declared=symbol_pins)
    pairs = check_pin_map(
        pin_map, symbol_pins=pins, ports=model.ports, unconnected=unconnected_ports
    )
    derived = spice_model.format_sim_pins(pairs)
    if len(derived) > _SIM_PINS_MAX:
        _unprocessable(
            ErrorCodes.EDA_SPICE_SIM_PINS_TOO_LONG,
            f"the derived Sim.Pins is {len(derived)} characters; the field holds {_SIM_PINS_MAX}",
        )

    binding = _upsert_binding(db, ws=ws, part=part, user_id=user_id)
    _set_model(binding, model, datafile)
    header = _apply_header(binding, text)
    if header is None and source:
        binding.model_source = source
    binding.unconnected_ports = _ports_in_order(model.ports, unconnected_ports)
    connected = dict(pairs)
    binding.pin_map = {pin: connected.get(pin) for pin in pins}
    binding.symbol_pins_source = pins_source
    binding.sim_pins_derived = derived
    binding.validated = True
    binding.validated_at = utcnow()
    binding.updated_by = user_id
    config.sim_pins = derived
    config.updated_by = user_id
    db.flush()

    warnings: list[str] = []
    if header is not None and header.pins and len(header.pins) != len(model.ports):
        warnings.append(
            f"header_pin_count_differs: the NG-010 header lists {len(header.pins)} pins "
            f"({' '.join(header.pins)}), the {model.kind} line has {len(model.ports)}"
        )
    return BindResult(binding=binding, config=config, datafile=datafile, warnings=warnings)


# ---------------------------------------------------------------------
# Write: an uploaded file version
# ---------------------------------------------------------------------


def record_upload(
    db: Session, *, ws: Any, part: Part, datafile: EdaDatafile, text: str, user_id: UUID | None
) -> bool:
    """Note what a SPICE file just uploaded for `part` says. Returns changed.

    Only when the part now points at this file. A first upload of a file
    with one model creates an UNVALIDATED binding carrying its subckt and
    ports (no pin_map). Different bytes than an existing binding was
    checked against mark it `validated = false` and refresh the parsed
    model, keeping the old pin_map for re-confirmation — never deleted.
    """
    config = _config(db, ws=ws, part_id=part.id)
    if config is None or config.spice_datafile_id != datafile.id:
        return False
    binding = _binding(db, ws=ws, part_id=part.id)
    if (
        binding is not None
        and binding.model_sha256 == datafile.sha256
        and binding.spice_datafile_id == datafile.id
    ):
        return False
    models = spice_model.parse_models(text)
    if binding is None:
        if len(models) != 1:
            return False
        binding = PartSpiceBinding(workspace_id=ws.id, part_id=part.id, created_by=user_id)
        db.add(binding)
    elif len(models) != 1:
        # Cannot tell which model the old binding meant; flag it only.
        binding.validated = False
        binding.updated_by = user_id
        db.flush()
        return True
    _set_model(binding, models[0], datafile)
    _apply_header(binding, text)
    binding.validated = False
    binding.updated_by = user_id
    db.flush()
    return True


# ---------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------


def binding_issues(
    binding: PartSpiceBinding, config: PartEda | None, datafile_sha: str | None
) -> list[str]:
    """Why a stored binding does not hold right now; empty when it does."""
    if binding.model_kind in _FILELESS_KINDS:
        return _fileless_issues(binding, config)
    issues: list[str] = []
    if not binding.validated:
        issues.append("not_confirmed" if binding.pin_map is None else "awaiting_reconfirmation")
    if config is None or config.spice_datafile_id is None:
        issues.append("model_detached")
    elif (
        config.spice_datafile_id != binding.spice_datafile_id
        or datafile_sha != binding.model_sha256
    ):
        issues.append("model_changed")
    if binding.validated and (config is None or config.sim_pins != binding.sim_pins_derived):
        issues.append("sim_pins_edited")
    return issues


def _fileless_issues(binding: PartSpiceBinding, config: PartEda | None) -> list[str]:
    issues = ["no_model"] if binding.model_kind == "none" else []
    if binding.model_kind == "native_primitive" and not binding.validated:
        issues.append("not_confirmed")
    if config is not None and config.spice_datafile_id is not None:
        # Recorded as file-less, yet a file is attached now.
        issues.append("model_file_attached")
    return issues


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def binding_payload(db: Session, *, ws: Any, config: PartEda | None, part_id: UUID) -> dict | None:
    """The `spice_binding` object `get_part_eda` reports, or None."""
    binding = _binding(db, ws=ws, part_id=part_id)
    if binding is None:
        if config is not None and config.sim_pins and config.sim_pins.strip():
            return {"source": "free-text", "validated": False, "sim_pins": config.sim_pins}
        return None
    datafile = _datafile(db, ws=ws, datafile_id=config.spice_datafile_id if config else None)
    issues = binding_issues(binding, config, datafile.sha256 if datafile else None)
    return {
        "source": "binding",
        "validated": not issues,
        "issues": issues,
        "subckt": binding.subckt,
        "model_kind": binding.model_kind,
        "device_type": binding.device_type,
        "params": binding.params,
        "fallback": binding.fallback,
        "ports": list(binding.ports or []),
        "pin_map": binding.pin_map,
        "unconnected_ports": binding.unconnected_ports,
        "sim_pins": binding.sim_pins_derived,
        "symbol_pins_source": binding.symbol_pins_source,
        "spice_datafile_id": str(binding.spice_datafile_id) if binding.spice_datafile_id else None,
        "model_sha256": binding.model_sha256,
        "model_source": binding.model_source,
        "build_requirement": binding.build_requirement,
        "grade": binding.grade,
        "header_pins": binding.header_pins,
        "validated_at": _iso(binding.validated_at),
    }


def _free_text_checks(sim_pins: str, text: str | None) -> tuple[list[str], dict]:
    """Compare free-text `sim_pins` with the attached file, if readable."""
    issues = ["free_text_unvalidated"]
    detail: dict[str, Any] = {}
    pairs = spice_model.parse_sim_pins(sim_pins)
    if pairs is None:
        issues.append("sim_pins_unparseable")
    if text is None:
        issues.append(_UNREADABLE)
        return issues, detail
    models = spice_model.parse_models(text)
    detail["models"] = [m.name for m in models[:20]]
    if len(models) != 1:
        issues.append("no_model_in_file" if not models else "model_ambiguous")
        return issues, detail
    model = models[0]
    detail["subckt"], detail["ports"] = model.name, list(model.ports)
    if pairs is None or not model.checkable:
        return issues, detail
    if len(pairs) != len(model.ports):
        issues.append("port_count_mismatch")
    known = {p.lower() for p in model.ports}
    unknown = [port for _, port in pairs if port.lower() not in known]
    if unknown:
        issues.append("port_unknown")
        detail["unknown_ports"] = unknown[:20]
    return issues, detail


def list_issues(
    db: Session, *, ws: Any, after: UUID | None, limit: int
) -> tuple[list[dict], UUID | None]:
    """Parts whose SPICE pin mapping is unvalidated, keyset-paged on part id.

    Selected in SQL (so a page is never short because of filtering in
    Python): free-text `sim_pins` without a binding, or a binding that is
    unconfirmed, points at another file or other bytes, or whose derived
    value was edited. Read-only — nothing here writes.
    """
    stmt = (
        select(Part, PartEda, PartSpiceBinding, EdaDatafile)
        .join(PartEda, and_(PartEda.part_id == Part.id, PartEda.workspace_id == ws.id))
        .outerjoin(
            PartSpiceBinding,
            and_(
                PartSpiceBinding.part_id == Part.id,
                PartSpiceBinding.workspace_id == ws.id,
            ),
        )
        .outerjoin(
            EdaDatafile,
            and_(
                EdaDatafile.id == PartEda.spice_datafile_id,
                EdaDatafile.workspace_id == ws.id,
            ),
        )
        .where(Part.workspace_id == ws.id)
        .where(Part.archived_at.is_(None))
        .where(
            or_(
                and_(
                    PartSpiceBinding.id.is_(None),
                    PartEda.sim_pins.is_not(None),
                    PartEda.sim_pins != "",
                ),
                and_(
                    PartSpiceBinding.model_kind.in_(_FILELESS_KINDS),
                    PartEda.spice_datafile_id.is_not(None),
                ),
                and_(
                    PartSpiceBinding.id.is_not(None),
                    PartSpiceBinding.model_kind.not_in(_FILELESS_KINDS),
                    or_(
                        PartSpiceBinding.validated.is_(False),
                        PartEda.spice_datafile_id.is_(None),
                        PartEda.spice_datafile_id.is_distinct_from(
                            PartSpiceBinding.spice_datafile_id
                        ),
                        EdaDatafile.sha256.is_distinct_from(PartSpiceBinding.model_sha256),
                        PartEda.sim_pins.is_distinct_from(PartSpiceBinding.sim_pins_derived),
                    ),
                ),
            )
        )
    )
    if after is not None:
        stmt = stmt.where(Part.id > after)
    rows = list(db.execute(stmt.order_by(Part.id).limit(limit + 1)).all())
    page = rows[:limit]
    texts: dict[str, str | None] = {}
    items = [_issue_item(*row, texts) for row in page]
    next_after = page[-1][0].id if len(rows) > limit else None
    return items, next_after


def _issue_item(part, config, binding, datafile, texts: dict[str, str | None]) -> dict:
    item: dict[str, Any] = {
        "part_id": str(part.id),
        "name": part.name,
        "mpn": part.mpn,
        "sim_pins": config.sim_pins,
        "spice_datafile_id": str(datafile.id) if datafile else None,
    }
    if binding is not None:
        item["source"] = "binding"
        item["issues"] = binding_issues(binding, config, datafile.sha256 if datafile else None)
        item["subckt"] = binding.subckt
        item["ports"] = list(binding.ports or [])
        return item
    item["source"] = "free-text"
    if datafile is None:
        item["issues"] = ["free_text_unvalidated", "no_model_attached"]
        return item
    if datafile.sha256 not in texts:
        texts[datafile.sha256] = read_model_text(datafile)
    issues, detail = _free_text_checks(config.sim_pins, texts[datafile.sha256])
    item["issues"] = issues
    item.update(detail)
    return item
