"""Per-version metadata and review state for a part's hosted EDA files (SM-010).

A part's four CAD slots — `symbol`, `footprint`, `model3d`, `spice` —
each resolve to at most one hosted row (`eda_symbols`, `eda_footprints`,
`eda_datafiles`). This module attaches an `EdaAssetMetadata` row to that
asset AND its current `sha256`, so replacing the bytes yields a version
with no metadata and `review.state == "unreviewed"`.

Slot resolution goes through the part's own `part_eda` row, the same way
`export_parts` reports hashes: `model3d` is the footprint's FIRST attached
model by `position` (the one KiCad places). A slot that points at the
user's own local library (`*_ref_external`) or is empty has no hosted file
and nothing to describe — writes to it are refused with
`eda.asset_not_attached`.

Two writers, deliberately different:

* `set_metadata` — descriptive fields, a MERGE (an omitted field is left
  alone, `""` / `[]` clears it). Member tokens may call it.
* `set_review` — the review fields. The route that calls it requires an
  admin/owner browser session; this module trusts that gate and does not
  re-check it.

Workspace isolation: every query filters on `ws.id`; asset rows are only
reached through the part's workspace-filtered `part_eda` row and are
re-checked against `ws.id` on load.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ErrorCodes, raise_http
from app.core.time import utcnow
from app.domain.eda.models import (
    ASSET_KINDS,
    MODEL_KINDS,
    REVIEW_STATES,
    EdaAssetMetadata,
    EdaDatafile,
    EdaFootprint,
    EdaFootprintModel,
    EdaSymbol,
    PartEda,
    PartSpiceBinding,
)
from app.domain.users.models import User

# Bounds on the free-form fields. Column widths for the strings; the list
# cap keeps one call from writing a novel into an ARRAY column.
_ORIGIN_MAX = 1000
_NOTE_MAX = 2000
_AGAINST_MAX = 500
_LIST_ITEM_MAX = {"simulators": 40, "valid_for": 120, "not_valid_for": 120}
_LIST_MAX_ITEMS = 20

METADATA_FIELDS = ("origin", "model_kind", "simulators", "valid_for", "not_valid_for", "note")


@dataclass(frozen=True)
class AssetRef:
    """One hosted file in one of a part's slots."""

    kind: str
    row: Any  # EdaSymbol | EdaFootprint | EdaDatafile
    fk: str  # which EdaAssetMetadata column points at it

    def metadata_filter(self):
        return (getattr(EdaAssetMetadata, self.fk) == self.row.id) & (
            EdaAssetMetadata.sha256 == self.row.sha256
        )


def _invalid(message: str, **fields: Any):
    raise_http(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        code=ErrorCodes.EDA_ASSET_METADATA_INVALID,
        message=message,
        **fields,
    )


def _config(db: Session, *, ws: Any, part_id: UUID) -> PartEda | None:
    return db.execute(
        select(PartEda)
        .where(PartEda.workspace_id == ws.id)
        .where(PartEda.part_id == part_id)
    ).scalars().first()


def _load(db: Session, *, ws: Any, Model: type, row_id: UUID | None):
    if row_id is None:
        return None
    return db.execute(
        select(Model).where(Model.workspace_id == ws.id).where(Model.id == row_id)
    ).scalars().first()


def _first_model(db: Session, *, ws: Any, footprint_id: UUID | None):
    if footprint_id is None:
        return None
    return db.execute(
        select(EdaDatafile)
        .join(EdaFootprintModel, EdaFootprintModel.datafile_id == EdaDatafile.id)
        .where(EdaFootprintModel.workspace_id == ws.id)
        .where(EdaFootprintModel.footprint_id == footprint_id)
        .where(EdaDatafile.workspace_id == ws.id)
        .order_by(EdaFootprintModel.position, EdaFootprintModel.id)
        .limit(1)
    ).scalars().first()


def resolve_assets(db: Session, *, ws: Any, config: PartEda | None) -> dict[str, AssetRef]:
    """The hosted file behind each of the part's slots, absent ones omitted."""
    if config is None:
        return {}
    found: dict[str, AssetRef] = {}
    symbol = _load(db, ws=ws, Model=EdaSymbol, row_id=config.symbol_id)
    if symbol is not None:
        found["symbol"] = AssetRef("symbol", symbol, "symbol_id")
    footprint = _load(db, ws=ws, Model=EdaFootprint, row_id=config.footprint_id)
    if footprint is not None:
        found["footprint"] = AssetRef("footprint", footprint, "footprint_id")
        model = _first_model(db, ws=ws, footprint_id=footprint.id)
        if model is not None:
            found["model3d"] = AssetRef("model3d", model, "datafile_id")
    spice = _load(db, ws=ws, Model=EdaDatafile, row_id=config.spice_datafile_id)
    if spice is not None and spice.kind == "spice":
        found["spice"] = AssetRef("spice", spice, "datafile_id")
    return found


def _resolve_one(db: Session, *, ws: Any, part, kind: str) -> AssetRef:
    if kind not in ASSET_KINDS:
        _invalid(f"kind must be one of {', '.join(ASSET_KINDS)}", field="kind")
    ref = resolve_assets(db, ws=ws, config=_config(db, ws=ws, part_id=part.id)).get(kind)
    if ref is None:
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.EDA_ASSET_NOT_ATTACHED,
            message=(
                f"the part has no hosted {kind} file to describe (the slot is "
                "empty or points at a local KiCad library)"
            ),
            kind=kind,
        )
    return ref


def _metadata_rows(
    db: Session, *, ws: Any, refs: Iterable[AssetRef]
) -> dict[tuple[str, UUID, str], EdaAssetMetadata]:
    refs = list(refs)
    if not refs:
        return {}
    rows = db.execute(
        select(EdaAssetMetadata)
        .where(EdaAssetMetadata.workspace_id == ws.id)
        .where(or_(*(ref.metadata_filter() for ref in refs)))
    ).scalars()
    out = {}
    for row in rows:
        fk = next(c for c in ("symbol_id", "footprint_id", "datafile_id") if getattr(row, c))
        out[(fk, getattr(row, fk), row.sha256)] = row
    return out


def _get_or_create(db: Session, *, ws: Any, ref: AssetRef, user_id: UUID | None):
    row = _metadata_rows(db, ws=ws, refs=[ref]).get((ref.fk, ref.row.id, ref.row.sha256))
    if row is not None:
        return row
    row = EdaAssetMetadata(
        workspace_id=ws.id,
        sha256=ref.row.sha256,
        review_state="unreviewed",
        created_by=user_id,
        updated_by=user_id,
    )
    setattr(row, ref.fk, ref.row.id)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        # A concurrent writer created the row for this version first.
        return _metadata_rows(db, ws=ws, refs=[ref])[(ref.fk, ref.row.id, ref.row.sha256)]
    return row


# ---------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------


def _header_origins(db: Session, *, ws: Any, part_id: UUID) -> dict[str, str]:
    """SM-009's NG-010 `model_source` per model hash, reused as a fallback
    `origin` for the SPICE slot rather than stored a second time."""
    rows = db.execute(
        select(PartSpiceBinding.model_sha256, PartSpiceBinding.model_source)
        .where(PartSpiceBinding.workspace_id == ws.id)
        .where(PartSpiceBinding.part_id == part_id)
        .where(PartSpiceBinding.model_source.is_not(None))
    ).all()
    return {sha: source for sha, source in rows}


def _reviewer_names(db: Session, rows: Iterable[EdaAssetMetadata]) -> dict[UUID, str]:
    ids = {r.reviewed_by for r in rows if r.reviewed_by is not None}
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.name).where(User.id.in_(ids))).all())


def _entry(
    ref: AssetRef,
    meta: EdaAssetMetadata | None,
    *,
    names: Mapping[UUID, str],
    header_origin: str | None,
) -> dict[str, Any]:
    origin = meta.origin if meta is not None else None
    origin_from = "metadata" if origin else None
    if not origin and ref.kind == "spice" and header_origin:
        origin, origin_from = header_origin, "spice_header"
    reviewer = meta.reviewed_by if meta is not None else None
    return {
        "id": str(ref.row.id),
        "name": ref.row.name,
        "sha256": ref.row.sha256,
        "source": ref.row.source,
        "origin": origin,
        "origin_from": origin_from,
        "model_kind": meta.model_kind if meta is not None else None,
        "simulators": list(meta.simulators or []) if meta is not None else [],
        "valid_for": list(meta.valid_for or []) if meta is not None else [],
        "not_valid_for": list(meta.not_valid_for or []) if meta is not None else [],
        "note": meta.note if meta is not None else None,
        "review": {
            "state": meta.review_state if meta is not None else "unreviewed",
            "by": str(reviewer) if reviewer is not None else None,
            "by_name": names.get(reviewer) if reviewer is not None else None,
            "at": (
                meta.reviewed_at.isoformat()
                if meta is not None and meta.reviewed_at is not None
                else None
            ),
            "against": meta.review_against if meta is not None else None,
        },
    }


def assets_payload(
    db: Session, *, ws: Any, part_id: UUID, config: PartEda | None
) -> dict[str, dict[str, Any]]:
    """`{kind: entry}` for every hosted slot of the part; empty when none.

    Three queries whatever the number of slots: the slot rows, their
    metadata, the reviewer names (plus one for the SPICE header origin).
    """
    refs = resolve_assets(db, ws=ws, config=config)
    if not refs:
        return {}
    metas = _metadata_rows(db, ws=ws, refs=refs.values())
    names = _reviewer_names(db, metas.values())
    headers = _header_origins(db, ws=ws, part_id=part_id) if "spice" in refs else {}
    return {
        kind: _entry(
            ref,
            metas.get((ref.fk, ref.row.id, ref.row.sha256)),
            names=names,
            header_origin=headers.get(ref.row.sha256),
        )
        for kind, ref in refs.items()
    }


def asset_payload(db: Session, *, ws: Any, part, kind: str) -> dict[str, Any]:
    return assets_payload(
        db, ws=ws, part_id=part.id, config=_config(db, ws=ws, part_id=part.id)
    )[kind]


# ---------------------------------------------------------------------
# Write: descriptive metadata
# ---------------------------------------------------------------------


def _clean_text(name: str, value: str, limit: int) -> str | None:
    if not isinstance(value, str):
        _invalid(f"{name} must be a string", field=name)
    value = value.strip()
    if len(value) > limit:
        _invalid(f"{name} is longer than {limit} characters", field=name)
    return value or None


def _clean_list(name: str, value: list[str]) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        _invalid(f"{name} must be a list of strings", field=name)
    limit = _LIST_ITEM_MAX[name]
    seen: list[str] = []
    for item in (v.strip() for v in value):
        if not item:
            continue
        if len(item) > limit:
            _invalid(f"each {name} entry is at most {limit} characters", field=name)
        if item not in seen:
            seen.append(item)
    if len(seen) > _LIST_MAX_ITEMS:
        _invalid(f"{name} holds at most {_LIST_MAX_ITEMS} entries", field=name)
    return seen or None


def clean_metadata(kind: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the PROVIDED fields; returns column → value (None clears)."""
    out: dict[str, Any] = {}
    for name, value in fields.items():
        if name not in METADATA_FIELDS:
            _invalid(f"unknown field {name}", field=name)
        if name == "origin":
            out[name] = _clean_text(name, value, _ORIGIN_MAX)
        elif name == "note":
            out[name] = _clean_text(name, value, _NOTE_MAX)
        elif name == "model_kind":
            cleaned = _clean_text(name, value, 12)
            if cleaned is not None and kind != "spice":
                _invalid("model_kind applies to SPICE models only", field=name)
            if cleaned is not None and cleaned not in MODEL_KINDS:
                _invalid(f"model_kind must be one of {', '.join(MODEL_KINDS)}", field=name)
            out[name] = cleaned
        else:
            out[name] = _clean_list(name, value)
    return out


def set_metadata(
    db: Session, *, ws: Any, part, kind: str, user_id: UUID | None, fields: Mapping[str, Any]
) -> tuple[AssetRef, list[str]]:
    """Merge `fields` into the metadata of the slot's CURRENT version.

    Returns the asset and the names of the fields written. Never touches
    the review columns.
    """
    ref = _resolve_one(db, ws=ws, part=part, kind=kind)
    cleaned = clean_metadata(kind, fields)
    if not cleaned:
        _invalid("nothing to set: pass at least one metadata field")
    row = _get_or_create(db, ws=ws, ref=ref, user_id=user_id)
    for column, value in cleaned.items():
        setattr(row, column, value)
    row.updated_by = user_id
    db.flush()
    return ref, sorted(cleaned)


# ---------------------------------------------------------------------
# Write: review (admin/owner session only — gated by the route)
# ---------------------------------------------------------------------


def set_review(
    db: Session,
    *,
    ws: Any,
    part,
    kind: str,
    user_id: UUID,
    state: str,
    against: str | None,
    expected_sha256: str | None,
) -> AssetRef:
    """Record a review verdict on the slot's CURRENT version.

    `expected_sha256` pins the verdict to the bytes the reviewer looked
    at: if the file was replaced meanwhile, the call is refused with
    `eda.asset_changed` instead of blessing a version nobody opened.
    `unreviewed` clears reviewer, time and `against`.
    """
    if state not in REVIEW_STATES:
        _invalid(f"state must be one of {', '.join(REVIEW_STATES)}", field="state")
    ref = _resolve_one(db, ws=ws, part=part, kind=kind)
    if expected_sha256 and expected_sha256 != ref.row.sha256:
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.EDA_ASSET_CHANGED,
            message="the file was replaced since it was opened; review the new version",
            sha256=ref.row.sha256,
        )
    cleaned_against = (
        _clean_text("against", against, _AGAINST_MAX) if against is not None else None
    )
    row = _get_or_create(db, ws=ws, ref=ref, user_id=user_id)
    row.review_state = state
    if state == "unreviewed":
        row.reviewed_by = row.reviewed_at = row.review_against = None
    else:
        row.reviewed_by = user_id
        row.reviewed_at = utcnow()
        row.review_against = cleaned_against
    row.updated_by = user_id
    db.flush()
    return ref
