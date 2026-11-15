"""When and how a linked 3D model gets a placement.

`model_fit` is the pure geometry. This module is the policy and the I/O
around it: which links need a placement, where the STEP and the
footprint bytes come from, and what to do when the geometry can't be
read (nothing — the link keeps a NULL placement and the package falls
back to the identity, which is what it always did).

The rule, everywhere: a footprint whose own bytes carry a `(model …)` for
this model (same file stem, any format, any path) already says where it
goes. Only a model the footprint is silent about gets a fitted
placement.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.eda import model_fit, preview3d, sexpr, storage
from app.domain.eda.models import EdaDatafile, EdaFootprintModel

_log = logging.getLogger(__name__)

_STEP = "step"
_WRL = "wrl"


def model_stem(path: str) -> str:
    """A model path's file stem, folded — `ABC.step` and `abc.wrl` match."""
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    return (base.rsplit(".", 1)[0] if "." in base else base).casefold()


def footprint_places(node: sexpr.Node, model_name: str) -> bool:
    """Whether the footprint's own bytes already place `model_name`."""
    stem = model_stem(model_name)
    return any(model_stem(path) == stem for path in sexpr.model_paths(node))


def fit_glb(footprint_node: sexpr.Node, glb: bytes) -> model_fit.Fit:
    return model_fit.fit(
        model_fit.glb_bounds(glb), model_fit.footprint_target(footprint_node)
    )


def read_footprint(workspace_id: UUID, footprint: Any) -> sexpr.Node | None:
    ext = storage.EXT_BY_KIND[storage.FOOTPRINT_KIND]
    path = storage.path_for(workspace_id, f"{footprint.sha256}.{ext}")
    try:
        with open(path, encoding="utf-8") as handle:
            return sexpr.parse(handle.read())
    except (OSError, UnicodeDecodeError, sexpr.SexprError):
        _log.warning("model placement: footprint %s unreadable", footprint.id)
        return None


def fit_stored(
    workspace_id: UUID,
    footprint_node: sexpr.Node,
    datafile: Any,
    *,
    write_cache: bool = True,
) -> model_fit.Fit | None:
    """Fit a stored STEP onto a parsed footprint, or None.

    `datafile` is anything with `id`, `kind` and `sha256` — an
    `EdaDatafile` row, or the importer's `CreatedRow` before the session
    has been flushed for reading.
    """
    if datafile.kind != _STEP:
        return None
    path = storage.path_for(workspace_id, f"{datafile.sha256}.{_STEP}")
    try:
        glb = preview3d.read_or_convert(
            workspace_id, datafile.sha256, path, write_cache=write_cache
        )
        return fit_glb(footprint_node, glb)
    except (
        preview3d.Preview3dError,
        model_fit.FitError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        IndexError,
    ):
        # Any geometry the fit can't read degrades to "no placement" —
        # linking must never fail because a model is odd.
        _log.warning("model placement: could not fit datafile %s", datafile.id)
        return None


def placement_for_link(
    db: Session,
    *,
    workspace_id: UUID,
    footprint: Any,
    datafile: EdaDatafile,
    write_cache: bool = True,
) -> dict | None:
    """The placement to store on a new footprint ↔ model link, or None.

    None when the footprint's bytes already place the model, or when the
    geometry can't be read. A WRL can't be measured (cascadio reads STEP
    only), so it copies the placement of a same-stem STEP already linked
    to the footprint — they are the same model in two formats.
    """
    node = read_footprint(workspace_id, footprint)
    if node is None or footprint_places(node, datafile.name):
        return None
    if datafile.kind == _WRL:
        return _sibling_placement(
            db, workspace_id=workspace_id, footprint_id=footprint.id, name=datafile.name
        )
    fitted = fit_stored(workspace_id, node, datafile, write_cache=write_cache)
    return fitted.placement.as_json() if fitted else None


def _sibling_placement(
    db: Session, *, workspace_id: UUID, footprint_id: UUID, name: str
) -> dict | None:
    stem = model_stem(name)
    rows = db.execute(
        select(EdaDatafile.name, EdaFootprintModel.placement)
        .join(
            EdaDatafile,
            (EdaDatafile.id == EdaFootprintModel.datafile_id)
            & (EdaDatafile.workspace_id == EdaFootprintModel.workspace_id),
        )
        .where(EdaFootprintModel.workspace_id == workspace_id)
        .where(EdaFootprintModel.footprint_id == footprint_id)
        .where(EdaDatafile.kind == _STEP)
        .where(EdaDatafile.archived_at.is_(None))
        .where(EdaFootprintModel.placement.is_not(None))
    ).all()
    return next((p for n, p in rows if model_stem(n) == stem), None)


def share_with_siblings(
    db: Session,
    *,
    workspace_id: UUID,
    footprint_id: UUID,
    name: str,
    placement: dict,
) -> list[EdaFootprintModel]:
    """Copy a STEP link's placement onto same-stem WRL links still NULL.

    A WRL linked BEFORE its STEP found no sibling to copy, so without this
    the package would append the WRL at the identity and then skip the
    STEP as "the same model already placed" — the original bug. Returns
    the links it changed.
    """
    stem = model_stem(name)
    rows = db.execute(
        select(EdaFootprintModel, EdaDatafile.name)
        .join(
            EdaDatafile,
            (EdaDatafile.id == EdaFootprintModel.datafile_id)
            & (EdaDatafile.workspace_id == EdaFootprintModel.workspace_id),
        )
        .where(EdaFootprintModel.workspace_id == workspace_id)
        .where(EdaFootprintModel.footprint_id == footprint_id)
        .where(EdaDatafile.kind == _WRL)
        .where(EdaFootprintModel.placement.is_(None))
    ).all()
    changed = [link for link, wrl_name in rows if model_stem(wrl_name) == stem]
    for link in changed:
        link.placement = placement
    return changed
