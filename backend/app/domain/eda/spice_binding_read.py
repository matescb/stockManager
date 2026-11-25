"""The read side of the SPICE binding (SM-009): what holds, what to fix.

`binding_payload` is the `spice_binding` object `get_part_eda` reports;
`list_issues` is `list_spice_binding_issues`. Whether a stored binding
still holds is computed HERE, against the live `part_eda` row — a
different file, different bytes or an edited `sim_pins` make it
unvalidated whichever path made the change. Nothing in this module
writes. The write side is `spice_binding.py`.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.domain.eda import spice_model
from app.domain.eda.models import EdaDatafile, PartEda, PartSpiceBinding
from app.domain.eda.spice_binding import (
    FILELESS_KINDS,
    binding_for_part,
    datafile_in_ws,
    read_model_text,
)
from app.domain.parts.models import Part

_UNREADABLE = "model_unreadable"


def binding_issues(
    binding: PartSpiceBinding, config: PartEda | None, datafile_sha: str | None
) -> list[str]:
    """Why a stored binding does not hold right now; empty when it does."""
    if binding.model_kind in FILELESS_KINDS:
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
    binding = binding_for_part(db, ws=ws, part_id=part_id)
    if binding is None:
        if config is not None and config.sim_pins and config.sim_pins.strip():
            return {"source": "free-text", "validated": False, "sim_pins": config.sim_pins}
        return None
    datafile = datafile_in_ws(db, ws=ws, datafile_id=config.spice_datafile_id if config else None)
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
        "fallback_token": binding.fallback_token,
        "native": binding.native,
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
        **_export_fields(binding.export_metadata),
        "validated_at": _iso(binding.validated_at),
    }


def _export_fields(meta: dict | None) -> dict[str, Any]:
    """The `ngspice-export/1` header fields, flattened for the agent."""
    if not meta:
        return {}
    ports = meta.get("ports") or []
    return {
        "export_schema": meta.get("export_schema"),
        "export_kind": meta.get("kind"),
        "build": meta.get("build"),
        "valid_for_basis": meta.get("valid_for_basis"),
        "port_functions": {p["port"]: p["function"] for p in ports} or None,
        "ports_on_symbol": {p["port"]: p["on_symbol"] for p in ports} or None,
        "suggested_unconnected_ports": meta.get("internally_unconnected_ports"),
        "channels": meta.get("channels"),
        "match": meta.get("match"),
        "corrections": meta.get("corrections"),
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
                    PartSpiceBinding.model_kind.in_(FILELESS_KINDS),
                    PartEda.spice_datafile_id.is_not(None),
                ),
                and_(
                    PartSpiceBinding.id.is_not(None),
                    PartSpiceBinding.model_kind.not_in(FILELESS_KINDS),
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
