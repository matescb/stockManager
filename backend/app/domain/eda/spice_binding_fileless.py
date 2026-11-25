"""File-less SPICE records: `native_primitive` and `none` (SM-009).

Split out of `spice_binding.py`, whose `bind` dispatches here for every
kind but `file`. A part without a model file is not blank:

* `native_primitive` — KiCad's built-in R / C / L (ports `+ -`) or D
  (`A K`) with `params`; the ngSPICE inventory manifest's `native`
  object is accepted as-is (letter → device_type, R/C/L value → params).
* `none` — no model, with `fallback` / `fallback_token`. `subckt` may
  name a library model that EXISTS but has no loadable export, with the
  manifest's `export_reason` ("does not load on any build: NG-012 …");
  `native_reason` says why a native element was not given a value.

The two reasons live in `export_metadata` (JSONB, ngSPICE-provided
metadata) rather than in new columns, so this needs no migration.
Neither kind touches `part_eda`: the KiCad HTTP library emits `Sim.*`
only for a part with a SPICE file.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import ErrorCodes
from app.core.time import utcnow
from app.domain.eda import spice_model
from app.domain.eda.models import PartEda
from app.domain.eda.spice_binding import (
    BindResult,
    _config,
    _symbol_pins,
    _unprocessable,
    _upsert_binding,
    check_pin_map,
)
from app.domain.parts.models import Part

_REASON_MAX = 1000


def bind_fileless(
    db: Session,
    *,
    ws: Any,
    part: Part,
    user_id: UUID | None,
    model_kind: str,
    pin_map: Mapping[str, str | None] | None,
    subckt: str | None,
    symbol_pins: Sequence[str] | None,
    device_type: str | None,
    params: str | None,
    fallback: str | None,
    source: str | None,
    fallback_token: str | None,
    native: Mapping[str, Any] | None,
    export_reason: str | None,
    native_reason: str | None,
) -> BindResult:
    """Store a `native_primitive` or `none` record; refusals change nothing."""
    if subckt and model_kind != "none":
        _unprocessable(
            ErrorCodes.EDA_SPICE_NOT_APPLICABLE,
            "subckt names a library model only for model_kind 'none' (exists, no export)",
        )
    native_record = _native_record(native, model_kind)
    if native_record is not None and model_kind == "native_primitive":
        device_type = device_type or native_record["letter"]
        params = params or _native_params(native_record)
    _check_fallback_token(fallback_token)
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
                "subckt (the library model that exists but does not load), "
                "export_reason, native, native_reason, fallback, fallback_token, source",
            )
        ports, pairs, pins_source = (), None, None
    binding = _upsert_binding(db, ws=ws, part=part, user_id=user_id)
    binding.model_kind = model_kind
    binding.subckt = subckt[:200] if subckt else None
    binding.device_type = device_type.upper() if device_type else None
    binding.ports = list(ports)
    binding.params = params or None
    binding.fallback = fallback or None
    binding.fallback_token = fallback_token or None
    binding.native = native_record
    binding.model_source = source or None
    binding.spice_datafile_id = binding.model_sha256 = None
    binding.build_requirement = binding.grade = binding.header_pins = None
    binding.export_metadata = _reasons(export_reason, native_reason)
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
    warnings = []
    if native_record and native_record["loses_behaviour"]:
        warnings.append(
            "native_loses_behaviour: "
            + (native_record["loses_behaviour_reason"] or "the primitive is a simplification")
        )
    return BindResult(binding=binding, config=config, datafile=None, warnings=warnings)


_FALLBACK_TOKENS = ("vendor_model_wanted", "native", "attended_ltspice")
_EXTERNAL_PREFIX = "external_model:"


def _check_fallback_token(token: str | None) -> None:
    """The ngspice-export/1 fallback vocabulary, or a refusal."""
    if token is None or token in _FALLBACK_TOKENS:
        return
    if token.startswith(_EXTERNAL_PREFIX) and 0 < len(token) - len(_EXTERNAL_PREFIX) <= 200:
        return
    _unprocessable(
        ErrorCodes.EDA_SPICE_FALLBACK_TOKEN_UNKNOWN,
        f"fallback_token {token[:60]!r} is not one of {', '.join(_FALLBACK_TOKENS)} "
        f"or {_EXTERNAL_PREFIX}<name>",
    )


def _native_record(native: Mapping[str, Any] | None, model_kind: str) -> dict | None:
    """The manifest's `native` object, checked and normalised for storage."""
    if native is None:
        return None
    if model_kind not in ("native_primitive", "none"):
        _unprocessable(
            ErrorCodes.EDA_SPICE_NOT_APPLICABLE,
            "native is for model_kind native_primitive or none",
        )
    letter = str(native.get("letter") or "").upper()
    if letter not in spice_model.NATIVE_PORTS:
        _unprocessable(
            ErrorCodes.EDA_SPICE_NATIVE_INVALID,
            f"native.letter {letter[:5]!r} is not one of {', '.join(spice_model.NATIVE_PORTS)}",
        )
    parameters = native.get("parameters")
    if parameters is not None:
        value = parameters.get("value") if isinstance(parameters, Mapping) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _unprocessable(
                ErrorCodes.EDA_SPICE_NATIVE_INVALID,
                "native.parameters needs a numeric value (with unit and source)",
            )
        parameters = {
            "value": value,
            "unit": _short(parameters.get("unit"), 10),
            "source": _short(parameters.get("source"), 500),
        }
    return {
        "letter": letter,
        "parameters": parameters,
        "parameters_reason": _short(native.get("parameters_reason"), 500),
        "loses_behaviour": bool(native.get("loses_behaviour")),
        "loses_behaviour_reason": _short(native.get("loses_behaviour_reason"), 500),
    }


def _short(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) > limit:
        _unprocessable(
            ErrorCodes.EDA_SPICE_FIELD_TOO_LONG,
            f"a text field is {len(text)} characters; the limit is {limit}",
        )
    return text


def _native_params(native: Mapping[str, Any]) -> str | None:
    """KiCad `Sim.Params` text for an R / C / L value, e.g. "c=1e-10"."""
    parameters = native.get("parameters")
    if not parameters or native["letter"] == "D":
        return None
    return f"{native['letter'].lower()}={parameters['value']:g}"


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


def _reasons(export_reason: str | None, native_reason: str | None) -> dict | None:
    reasons = {
        "export_reason": _short(export_reason, _REASON_MAX),
        "native_reason": _short(native_reason, _REASON_MAX),
    }
    return {k: v for k, v in reasons.items() if v} or None
