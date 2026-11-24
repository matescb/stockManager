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
from app.domain.eda import kicad_refs, pin_pad, storage
from app.domain.eda.models import EdaFootprint, EdaSymbol, PartEda
from app.domain.parts.models import Part


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
        out[part.id] = _payload(ws_id, symbol, footprint)
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


def _payload(ws_id: UUID, symbol: _Slot, footprint: _Slot) -> dict[str, Any]:
    blocked = _not_checkable(symbol, footprint)
    if blocked is not None:
        result = pin_pad.PinPadResult(
            verdict=pin_pad.NOT_CHECKABLE, reason=blocked[0], detail=blocked[1]
        )
    else:
        result = _check(ws_id, symbol.sha256 or "", footprint.sha256 or "")
    payload: dict[str, Any] = {
        "verdict": result.verdict,
        "symbol_sha256": symbol.sha256,
        "footprint_sha256": footprint.sha256,
        "symbol_ref": symbol.ref,
        "footprint_ref": footprint.ref,
        "findings": result.findings(),
        "checked_at": datetime.now(UTC).isoformat(),
    }
    if result.reason is not None:
        payload["reason"] = result.reason
        payload["detail"] = result.detail
    return payload


def _check(ws_id: UUID, symbol_sha: str, footprint_sha: str) -> pin_pad.PinPadResult:
    def load() -> tuple[bytes, bytes]:
        return (
            _read(ws_id, symbol_sha, storage.SYMBOL_KIND),
            _read(ws_id, footprint_sha, storage.FOOTPRINT_KIND),
        )

    try:
        return pin_pad.check_cached(symbol_sha, footprint_sha, load)
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
