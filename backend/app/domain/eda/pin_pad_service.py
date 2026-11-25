"""Find a part's symbol and footprint files and run the pin/pad check.

SM-008. The comparison itself lives in `pin_pad.py`; this module only
decides WHICH two files a part uses and reads them. Read-only by
construction: it selects and opens files, never writes, and stores no
verdict — the answer is recomputed from the current files on every
call, so it is never stale and carries the hashes it was computed on.

A slot resolves the way KiCad will see it: a hosted entry
(`symbol_id` / `footprint_id`), else the part's own external reference
(`*_ref_external`), else the category default (`default_*_ref`, which
is also an external `LibNick:Entry`), else nothing. Only two hosted
files can be compared; an external one lives in the user's local KiCad
library and its file is not here.

Every lookup filters on the workspace, including the joined entry rows
— an FK alone does not prove the row is ours (CLAUDE.md).

SM-008b adds two inputs, both read here so the pure check stays pure:

* the part's category, for the `shell` class — a category whose name,
  library slug or `J` reference prefix says "connector";
* `pin_pad_acceptances` — a human's acceptance of a finding on THESE two
  files. An acceptance whose hashes match the current files sets that
  finding aside (it is shown with `accepted`, and no longer counts
  toward the verdict); one recorded on other files — or for a finding
  whose class has changed since — is listed under
  `stale_acceptances` and excuses nothing.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.categories.models import PartCategory
from app.domain.eda import kicad_refs, pin_pad, pin_pad_classify, storage
from app.domain.eda.models import EdaFootprint, EdaSymbol, PartEda, PinPadAcceptance
from app.domain.parts.models import Part
from app.domain.users.models import User

_CONNECTOR_REFDES = "J"


@dataclass(frozen=True)
class _Slot:
    ref: str | None = None
    sha256: str | None = None
    external: bool = False


_MISSING = _Slot()


def check_parts(
    db: Session, ws_id: UUID, parts: Sequence[Part]
) -> dict[UUID, dict[str, Any]]:
    """The `pin_pad_check` payload for each part, in a fixed few queries."""
    if not parts:
        return {}
    configs = {
        row.part_id: row
        for row in db.execute(
            select(PartEda)
            .where(PartEda.workspace_id == ws_id)
            .where(PartEda.part_id.in_([p.id for p in parts]))
        ).scalars()
    }
    symbols = _rows(db, ws_id, EdaSymbol, [c.symbol_id for c in configs.values()])
    footprints = _rows(db, ws_id, EdaFootprint, [c.footprint_id for c in configs.values()])
    categories = _rows(
        db,
        ws_id,
        PartCategory,
        [p.category_id for p in parts]
        + [r.category_id for r in (*symbols.values(), *footprints.values())],
    )
    acceptances = _acceptances(db, ws_id, [p.id for p in parts])
    out: dict[UUID, dict[str, Any]] = {}
    for part in parts:
        config = configs.get(part.id)
        category = categories.get(part.category_id)
        symbol = _slot(
            symbols.get(config.symbol_id) if config else None,
            config.symbol_ref_external if config else None,
            category.default_symbol_ref if category else None,
            categories,
        )
        footprint = _slot(
            footprints.get(config.footprint_id) if config else None,
            config.footprint_ref_external if config else None,
            category.default_footprint_ref if category else None,
            categories,
        )
        out[part.id] = _payload(
            ws_id, symbol, footprint, _is_connector(category), acceptances.get(part.id, [])
        )
    return out


def _is_connector(category: PartCategory | None) -> bool:
    if category is None:
        return False
    if (category.refdes_prefix or "").strip().upper() == _CONNECTOR_REFDES:
        return True
    return pin_pad_classify.is_connector_text(f"{category.name} {category.library_slug}")


def _acceptances(
    db: Session, ws_id: UUID, part_ids: list[UUID]
) -> dict[UUID, list[dict[str, Any]]]:
    """Every acceptance of these parts, newest first, with the accepter's name."""
    rows = db.execute(
        select(PinPadAcceptance, User.name)
        .outerjoin(User, User.id == PinPadAcceptance.accepted_by)
        .where(PinPadAcceptance.workspace_id == ws_id)
        .where(PinPadAcceptance.part_id.in_(part_ids))
        .order_by(PinPadAcceptance.accepted_at.desc())
    ).all()
    out: dict[UUID, list[dict[str, Any]]] = {}
    for row, name in rows:
        out.setdefault(row.part_id, []).append(
            {
                "finding_kind": row.finding_kind,
                "number": row.number,
                "class": row.finding_class,
                "symbol_sha256": row.symbol_sha256,
                "footprint_sha256": row.footprint_sha256,
                "by": str(row.accepted_by) if row.accepted_by else None,
                "by_name": name,
                "at": row.accepted_at.isoformat(),
                "note": row.note,
            }
        )
    return out


def _rows(db: Session, ws_id: UUID, model: type, ids: list[UUID | None]) -> dict[UUID, Any]:
    wanted = [i for i in ids if i is not None]
    if not wanted:
        return {}
    return {
        row.id: row
        for row in db.execute(
            select(model).where(model.workspace_id == ws_id).where(model.id.in_(wanted))
        ).scalars()
    }


def _slot(
    hosted: Any, own_external: str | None, default_external: str | None,
    categories: dict[UUID, PartCategory],
) -> _Slot:
    if hosted is not None:
        category = categories.get(hosted.category_id)
        slug = category.library_slug if category is not None else None
        return _Slot(ref=kicad_refs.entry_ref(hosted.name, slug), sha256=hosted.sha256)
    external = own_external or default_external
    if external:
        return _Slot(ref=external, external=True)
    return _MISSING


def _not_checkable(symbol: _Slot, footprint: _Slot) -> tuple[str, str] | None:
    for side, slot, missing, ext in (
        ("symbol", symbol, pin_pad.REASON_SYMBOL_MISSING, pin_pad.REASON_SYMBOL_EXTERNAL),
        (
            "footprint",
            footprint,
            pin_pad.REASON_FOOTPRINT_MISSING,
            pin_pad.REASON_FOOTPRINT_EXTERNAL,
        ),
    ):
        if slot.external:
            return ext, f"the {side} is in the user's local KiCad library; its file is not here"
        if slot.sha256 is None:
            return missing, f"the part has no {side}"
    return None


def _payload(
    ws_id: UUID,
    symbol: _Slot,
    footprint: _Slot,
    connector: bool,
    acceptances: list[dict[str, Any]],
) -> dict[str, Any]:
    blocked = _not_checkable(symbol, footprint)
    if blocked is not None:
        result = pin_pad.PinPadResult(
            verdict=pin_pad.NOT_CHECKABLE, reason=blocked[0], detail=blocked[1]
        )
    else:
        result = _check(ws_id, symbol.sha256 or "", footprint.sha256 or "", connector)
    current = _applicable(result, symbol, footprint, acceptances)
    applied = _apply(result, current)
    standing = [f for f in applied if "accepted" not in f]
    accepted = {(f["kind"], f["number"]) for f in applied if "accepted" in f}
    payload: dict[str, Any] = {
        "verdict": (
            result.verdict
            if result.verdict == pin_pad.NOT_CHECKABLE
            else pin_pad.verdict_for(result.classified, accepted)
        ),
        "symbol_sha256": symbol.sha256,
        "footprint_sha256": footprint.sha256,
        "symbol_ref": symbol.ref,
        "footprint_ref": footprint.ref,
        "findings": {**result.findings(), "classified": applied},
        "electrical_count": sum(1 for f in standing if f["class"] == pin_pad.ELECTRICAL),
        "warnings_count": sum(1 for f in standing if f["class"] != pin_pad.ELECTRICAL),
        "accepted_count": len(applied) - len(standing),
        "stale_acceptances": [dict(a) for a in acceptances if a not in current],
        "checked_at": datetime.now(UTC).isoformat(),
    }
    if result.reason is not None:
        payload["reason"] = result.reason
        payload["detail"] = result.detail
    return payload


def _applicable(
    result: pin_pad.PinPadResult,
    symbol: _Slot,
    footprint: _Slot,
    acceptances: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Acceptances that still excuse their finding.

    Both hashes must match the current files AND the class recorded at
    acceptance must equal the finding's CURRENT class. The class can
    change on the same bytes (the part leaves a connector category, or
    the rules change); a person accepted "a shell tab", not whatever the
    pad reads as now — so any other acceptance is stale and the finding
    counts normally.
    """
    files = (symbol.sha256, footprint.sha256)
    classes = {f.key: f.cls for f in result.classified}
    return [
        a for a in acceptances
        if (a["symbol_sha256"], a["footprint_sha256"]) == files
        and classes.get((a["finding_kind"], a["number"])) == a["class"]
    ]


def _apply(
    result: pin_pad.PinPadResult, current: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """`classified` as dicts, each accepted one carrying `accepted`."""
    by_key = {}
    for a in current:  # newest first: keep the newest per key
        by_key.setdefault((a["finding_kind"], a["number"]), a)
    out = []
    for finding in result.classified:
        item = finding.as_dict()
        hit = by_key.get(finding.key)
        if hit is not None:
            item["accepted"] = {k: hit[k] for k in ("by", "by_name", "at", "note")}
        out.append(item)
    return out


def _check(
    ws_id: UUID, symbol_sha: str, footprint_sha: str, connector: bool = False
) -> pin_pad.PinPadResult:
    def load() -> tuple[bytes, bytes]:
        return (
            _read(ws_id, symbol_sha, storage.SYMBOL_KIND),
            _read(ws_id, footprint_sha, storage.FOOTPRINT_KIND),
        )

    try:
        return pin_pad.check_cached(symbol_sha, footprint_sha, load, connector)
    except pin_pad.NotCheckable as exc:
        return pin_pad.PinPadResult(
            verdict=pin_pad.NOT_CHECKABLE, reason=exc.reason, detail=exc.detail
        )


def _read(ws_id: UUID, sha: str, kind: str) -> bytes:
    filename = f"{sha}.{storage.EXT_BY_KIND[kind]}"
    try:
        with open(storage.path_for(ws_id, filename), "rb") as fh:
            return fh.read(storage.max_bytes_for(kind) + 1)
    except OSError:
        reason = (
            pin_pad.REASON_SYMBOL_UNREADABLE
            if kind == storage.SYMBOL_KIND
            else pin_pad.REASON_FOOTPRINT_UNREADABLE
        )
        raise pin_pad.NotCheckable(reason, f"the stored {kind} file is missing") from None
