"""EDA library CRUD and per-part EDA configuration.

Every function is workspace-scoped: reads filter on `ws.id`, lookups go
through `assert_in_workspace` so a foreign UUID is a 404 rather than a
cross-tenant read (ADR-0002). Writes `db.flush()` — the `get_db`
dependency owns the commit.

The three library tables (symbols, footprints, data files) differ only
in which columns take part in their uniqueness rule, so list / get /
rename / archive / restore are written once against a model parameter
and described by `_LIBRARY_META` rather than three times each. The
per-model behaviour that genuinely differs — what a valid upload looks
like, whether a category may be attached — stays at the call site.
"""
from __future__ import annotations

from typing import Any, NamedTuple
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api._helpers import assert_in_workspace
from app.core.errors import ErrorCodes, raise_http
from app.core.time import utcnow
from app.domain.categories.models import PartCategory
from app.domain.eda import sexpr, storage
from app.domain.eda.models import (
    EdaDatafile,
    EdaFootprint,
    EdaFootprintModel,
    EdaSymbol,
    PartEda,
)
from app.domain.eda.schemas import PartEdaIn

# A library entry's `name` is NOT NULL, so an explicit `null` in a PATCH
# can't mean anything — reject it honestly rather than silently dropping
# it (a silent no-op would return 200 while ignoring the request).
_NON_NULLABLE_PATCH_FIELDS = frozenset({"name"})

# 3D models attach to footprints; a SPICE model attaches to a part. The
# tables are shared, so the kind has to be checked at each seam.
_MODEL_KINDS = ("step", "wrl")
_SPICE_KIND = "spice"


class _Meta(NamedTuple):
    """Per-model facts the generic helpers need."""

    label: str
    not_found: str
    unique_index: str
    # Data files are unique per (workspace, kind, name); the other two
    # are unique per (workspace, name).
    kind_scoped: bool


_LIBRARY_META: dict[type, _Meta] = {
    EdaSymbol: _Meta(
        label="symbol",
        not_found=ErrorCodes.EDA_SYMBOL_NOT_FOUND,
        unique_index="uq_eda_symbols_ws_name",
        kind_scoped=False,
    ),
    EdaFootprint: _Meta(
        label="footprint",
        not_found=ErrorCodes.EDA_FOOTPRINT_NOT_FOUND,
        unique_index="uq_eda_footprints_ws_name",
        kind_scoped=False,
    ),
    EdaDatafile: _Meta(
        label="data file",
        not_found=ErrorCodes.EDA_DATAFILE_NOT_FOUND,
        unique_index="uq_eda_datafiles_ws_kind_name",
        kind_scoped=True,
    ),
}


# ---------------------------------------------------------------------
# Shared lookups + conflict handling
# ---------------------------------------------------------------------


def _active_by_name(
    db: Session,
    *,
    ws: Any,
    Model: type,
    name: str,
    kind: str | None = None,
    exclude_id: UUID | None = None,
):
    meta = _LIBRARY_META[Model]
    stmt: Select = (
        select(Model)
        .where(Model.workspace_id == ws.id)
        .where(Model.archived_at.is_(None))
        .where(Model.name == name)
    )
    if meta.kind_scoped:
        stmt = stmt.where(Model.kind == kind)
    if exclude_id is not None:
        stmt = stmt.where(Model.id != exclude_id)
    return db.execute(stmt.limit(1)).scalars().first()


def _raise_name_conflict(Model: type, existing) -> None:
    meta = _LIBRARY_META[Model]
    raise_http(
        status.HTTP_409_CONFLICT,
        code=ErrorCodes.EDA_NAME_CONFLICT,
        message=f'{meta.label} "{existing.name}" already exists',
        existing_id=str(existing.id),
        existing_name=existing.name,
    )


def _assert_name_available(
    db: Session,
    *,
    ws: Any,
    Model: type,
    name: str,
    kind: str | None = None,
    exclude_id: UUID | None = None,
) -> None:
    """Pre-flight the partial unique index so the caller gets a 409
    naming the row it collided with, instead of a bare IntegrityError."""
    clash = _active_by_name(
        db, ws=ws, Model=Model, name=name, kind=kind, exclude_id=exclude_id
    )
    if clash is not None:
        _raise_name_conflict(Model, clash)


def _raise_for_unique_violation(exc: IntegrityError, Model: type, *, name: str) -> None:
    """Map a lost race on the partial unique index onto the same 409 the
    pre-check would have produced. Two concurrent uploads both pass
    `_assert_name_available` and only one reaches COMMIT; without this
    the loser gets a 500."""
    meta = _LIBRARY_META[Model]
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    if getattr(diag, "constraint_name", None) == meta.unique_index:
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.EDA_NAME_CONFLICT,
            message=f'{meta.label} "{name}" already exists',
        )


def _assert_category(db: Session, *, ws: Any, category_id: UUID | None, changed: bool):
    """Validate a `category_id` against the workspace.

    Rejecting an ARCHIVED category only when the value actually changes
    mirrors `parts`: the CAD tab round-trips the current `category_id`
    with every save, so refusing an unchanged (since-archived) value
    would brick the form.
    """
    if category_id is None:
        return None
    category = assert_in_workspace(db, PartCategory, category_id, ws.id, label="category")
    if changed and category.archived_at is not None:
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.CATEGORY_ARCHIVED,
            message=f'category "{category.name}" is archived',
            existing_id=str(category.id),
        )
    return category


# ---------------------------------------------------------------------
# Library entries — list / get / upload / rename / archive / restore
# ---------------------------------------------------------------------


def list_entries(
    db: Session,
    *,
    ws: Any,
    Model: type,
    include_archived: bool = False,
    limit: int = 200,
) -> list:
    stmt = select(Model).where(Model.workspace_id == ws.id)
    if not include_archived:
        stmt = stmt.where(Model.archived_at.is_(None))
    if _LIBRARY_META[Model].kind_scoped:
        stmt = stmt.order_by(Model.kind.asc(), Model.name.asc())
    else:
        stmt = stmt.order_by(Model.name.asc())
    return list(db.execute(stmt.limit(limit)).scalars())


def get_entry(db: Session, *, ws: Any, Model: type, entry_id: UUID):
    """Fetch one entry, archived included (so the restore path still
    resolves). 404 on miss *or* cross-workspace, re-coded to the
    model-specific `eda_*.not_found` the way `_parts_shared.get_part`
    does, so the frontend can switch on it."""
    meta = _LIBRARY_META[Model]
    try:
        return assert_in_workspace(db, Model, entry_id, ws.id, label=meta.label)
    except HTTPException as exc:
        # Only re-code the 404; if assert_in_workspace ever grows another
        # status (403/400), let it through untouched instead of silently
        # rewriting it into a not-found.
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=meta.not_found,
            message=f"{meta.label} not found",
        )


def upload_entry(
    db: Session,
    *,
    ws: Any,
    Model: type,
    user_id: UUID | None,
    name: str,
    sha256: str,
    size_bytes: int,
    kind: str | None = None,
    category_id: UUID | None = None,
    source: str = "manual",
) -> tuple[Any, bool]:
    """Record an uploaded file. Returns `(row, created)`.

    Re-uploading identical bytes under a name that already holds them is
    a no-op returning the existing row — the store is content-addressed,
    so there is nothing to write, and the route answers 200 instead of
    201. Note that a `category_id` sent on that path is NOT applied: the
    row is returned untouched, and re-filing an entry is a PATCH.

    A name already held by *different* bytes is a 409: the caller has to
    rename or archive the old entry, because a KiCad library can't carry
    two entries with one name.
    """
    # Validate the category BEFORE the dedupe early-return so a foreign
    # or unknown category_id 404s on every path — it is still not APPLIED
    # on the dedupe path (re-filing an entry is a PATCH), but silently
    # ignoring an invalid reference was inconsistent with create.
    _assert_category(db, ws=ws, category_id=category_id, changed=True)

    existing = _active_by_name(db, ws=ws, Model=Model, name=name, kind=kind)
    if existing is not None:
        if existing.sha256 == sha256:
            return existing, False
        _raise_name_conflict(Model, existing)

    values: dict[str, Any] = {
        "workspace_id": ws.id,
        "name": name,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "source": source,
        "created_by": user_id,
        "updated_by": user_id,
    }
    if _LIBRARY_META[Model].kind_scoped:
        values["kind"] = kind
    else:
        values["category_id"] = category_id

    row = Model(**values)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        _raise_for_unique_violation(exc, Model, name=name)
        raise
    return row, True


def update_entry(
    db: Session,
    *,
    ws: Any,
    Model: type,
    entry_id: UUID,
    user_id: UUID | None,
    payload,
):
    entry = get_entry(db, ws=ws, Model=Model, entry_id=entry_id)
    original_name = entry.name
    data = payload.model_dump(exclude_unset=True)
    for field in _NON_NULLABLE_PATCH_FIELDS:
        if field in data and data[field] is None:
            raise_http(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=ErrorCodes.EDA_FIELD_NOT_NULLABLE,
                message=f"{field} cannot be null",
            )

    if "category_id" in data:
        _assert_category(
            db,
            ws=ws,
            category_id=data["category_id"],
            changed=data["category_id"] != entry.category_id,
        )

    name = data.get("name", entry.name)
    # An archived entry holds no name — it was freed on archive — so
    # renaming one can't collide with anything. Checking anyway would
    # reject a rename that `restore` is about to reject more clearly.
    if entry.archived_at is None:
        _assert_name_available(
            db,
            ws=ws,
            Model=Model,
            name=name,
            kind=getattr(entry, "kind", None),
            exclude_id=entry.id,
        )

    for key, value in data.items():
        setattr(entry, key, value)
    entry.updated_by = user_id
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        _raise_for_unique_violation(exc, Model, name=name)
        raise

    # The row's `name` and the entry name inside the stored file must
    # agree — file serving exposes the bytes today and phase 5 resolves
    # `LibNick:Entry` references against them. Datafiles carry no
    # embedded name, so only symbols/footprints are rewritten.
    if entry.name != original_name and Model in (EdaSymbol, EdaFootprint):
        _rewrite_stored_entry_name(entry, Model)
    return entry


def _rewrite_stored_entry_name(entry: Any, Model: type) -> None:
    """Re-emit the stored s-expression under the row's new name.

    Storage is content-addressed, so the rewrite lands at a new sha and
    the row's `sha256`/`size_bytes` move with it. The old blob stays on
    disk unreferenced — consistent with the repo-wide no-sweeper stance
    (ADR-0005).
    """
    kind = storage.SYMBOL_KIND if Model is EdaSymbol else storage.FOOTPRINT_KIND
    ext = storage.EXT_BY_KIND[kind]
    path = storage.path_for(entry.workspace_id, f"{entry.sha256}.{ext}")
    with open(path, encoding="utf-8") as fh:
        node = sexpr.parse(fh.read())
    data = sexpr.emit(sexpr.rename(node, entry.name)).encode("utf-8")
    entry.sha256, entry.size_bytes = storage.store(
        entry.workspace_id, data, kind=kind
    )


def archive_entry(
    db: Session, *, ws: Any, Model: type, entry_id: UUID, user_id: UUID | None
):
    entry = get_entry(db, ws=ws, Model=Model, entry_id=entry_id)
    if entry.archived_at is None:
        entry.archived_at = utcnow()
        entry.updated_by = user_id
        db.flush()
    return entry


def restore_entry(
    db: Session, *, ws: Any, Model: type, entry_id: UUID, user_id: UUID | None
):
    """Un-archive. The name was freed on archive, so another row may have
    taken it in the meantime — that's a 409, not a silent IntegrityError
    at commit time."""
    entry = get_entry(db, ws=ws, Model=Model, entry_id=entry_id)
    if entry.archived_at is None:
        return entry
    _assert_name_available(
        db,
        ws=ws,
        Model=Model,
        name=entry.name,
        kind=getattr(entry, "kind", None),
        exclude_id=entry.id,
    )
    entry.archived_at = None
    entry.updated_by = user_id
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        _raise_for_unique_violation(exc, Model, name=entry.name)
        raise
    return entry


# ---------------------------------------------------------------------
# Footprint ↔ 3D model links
# ---------------------------------------------------------------------


def list_footprint_models(db: Session, *, ws: Any, footprint) -> list[EdaFootprintModel]:
    return list(
        db.execute(
            select(EdaFootprintModel)
            .where(EdaFootprintModel.workspace_id == ws.id)
            .where(EdaFootprintModel.footprint_id == footprint.id)
            .order_by(EdaFootprintModel.position.asc())
        ).scalars()
    )


def link_footprint_model(
    db: Session,
    *,
    ws: Any,
    footprint_id: UUID,
    datafile_id: UUID,
    position: int,
    user_id: UUID | None,
):
    """Attach a 3D model to a footprint. Returns the footprint.

    Idempotent: re-linking a pair updates its position rather than
    tripping `uq_eda_footprint_model`, so a client that replays a save
    gets 200 and the position it asked for.
    """
    footprint = get_entry(db, ws=ws, Model=EdaFootprint, entry_id=footprint_id)
    datafile = get_entry(db, ws=ws, Model=EdaDatafile, entry_id=datafile_id)
    if datafile.kind not in _MODEL_KINDS:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCodes.EDA_UNSUPPORTED_KIND,
            message=(
                f'"{datafile.name}" is a {datafile.kind} model — only STEP and '
                "WRL files attach to a footprint"
            ),
        )

    link = db.execute(
        select(EdaFootprintModel)
        .where(EdaFootprintModel.workspace_id == ws.id)
        .where(EdaFootprintModel.footprint_id == footprint.id)
        .where(EdaFootprintModel.datafile_id == datafile.id)
    ).scalars().first()
    if link is None:
        db.add(
            EdaFootprintModel(
                workspace_id=ws.id,
                footprint_id=footprint.id,
                datafile_id=datafile.id,
                position=position,
            )
        )
    else:
        link.position = position
    footprint.updated_by = user_id
    db.flush()
    return footprint


def unlink_footprint_model(
    db: Session,
    *,
    ws: Any,
    footprint_id: UUID,
    datafile_id: UUID,
    user_id: UUID | None,
):
    """Detach a 3D model. Returns the footprint.

    Deleting a link that isn't there succeeds — DELETE is idempotent,
    and the caller's intent ("this pair is not linked") holds either way.
    """
    footprint = get_entry(db, ws=ws, Model=EdaFootprint, entry_id=footprint_id)
    link = db.execute(
        select(EdaFootprintModel)
        .where(EdaFootprintModel.workspace_id == ws.id)
        .where(EdaFootprintModel.footprint_id == footprint.id)
        .where(EdaFootprintModel.datafile_id == datafile_id)
    ).scalars().first()
    if link is not None:
        db.delete(link)
        footprint.updated_by = user_id
        db.flush()
    return footprint


# ---------------------------------------------------------------------
# Per-part EDA configuration
# ---------------------------------------------------------------------


def get_part_eda(db: Session, *, ws: Any, part) -> PartEda | None:
    return db.execute(
        select(PartEda)
        .where(PartEda.workspace_id == ws.id)
        .where(PartEda.part_id == part.id)
    ).scalars().first()


def _resolve_ref(
    db: Session,
    *,
    ws: Any,
    Model: type,
    entry_id: UUID | None,
    external: str | None,
    slot: str,
):
    """Validate one symbol/footprint slot of a `PartEdaIn`.

    Both halves set is a 422: they mean different things (host the
    definition here vs. name one in the user's local libraries) and
    picking a winner silently would hide a real client bug. An archived
    entry is a 409 — it exists, it's just been retired, and the caller
    needs to restore it or choose another.
    """
    if entry_id is not None and external is not None:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCodes.EDA_REF_CONFLICT,
            message=(
                f"set either {slot}_id (a hosted {slot}) or "
                f"{slot}_ref_external (a KiCad LibNick:Entry), not both"
            ),
            slot=slot,
        )
    if entry_id is None:
        return None
    entry = get_entry(db, ws=ws, Model=Model, entry_id=entry_id)
    if entry.archived_at is not None:
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.EDA_ARCHIVED,
            message=f'{_LIBRARY_META[Model].label} "{entry.name}" is archived',
            existing_id=str(entry.id),
        )
    return entry


def upsert_part_eda(
    db: Session, *, ws: Any, part, user_id: UUID | None, payload: PartEdaIn
) -> PartEda:
    """Write the part's EDA configuration, creating the row if absent.

    A full replacement — see `PartEdaIn`'s docstring. Every column is
    written from the payload, so a field the caller omits is reset to
    its default rather than left at whatever it held before.
    """
    _resolve_ref(
        db,
        ws=ws,
        Model=EdaSymbol,
        entry_id=payload.symbol_id,
        external=payload.symbol_ref_external,
        slot="symbol",
    )
    _resolve_ref(
        db,
        ws=ws,
        Model=EdaFootprint,
        entry_id=payload.footprint_id,
        external=payload.footprint_ref_external,
        slot="footprint",
    )
    if payload.spice_datafile_id is not None:
        spice = get_entry(
            db, ws=ws, Model=EdaDatafile, entry_id=payload.spice_datafile_id
        )
        if spice.archived_at is not None:
            raise_http(
                status.HTTP_409_CONFLICT,
                code=ErrorCodes.EDA_ARCHIVED,
                message=f'data file "{spice.name}" is archived',
                existing_id=str(spice.id),
            )
        if spice.kind != _SPICE_KIND:
            raise_http(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=ErrorCodes.EDA_UNSUPPORTED_KIND,
                message=(
                    f'"{spice.name}" is a {spice.kind} model — spice_datafile_id '
                    "needs a SPICE model"
                ),
            )

    config = get_part_eda(db, ws=ws, part=part)
    if config is None:
        config = PartEda(workspace_id=ws.id, part_id=part.id, created_by=user_id)
        try:
            with db.begin_nested():
                db.add(config)
                db.flush()
        except IntegrityError as exc:
            # Lost race on `uq_part_eda_part`: two saves for the same part
            # both found no row and only one reached the INSERT. Recover by
            # writing onto the row that won, so a double-click is
            # last-writer-wins rather than a 500.
            if not _is_part_eda_conflict(exc):
                raise
            config = get_part_eda(db, ws=ws, part=part)
            if config is None:
                raise

    _apply_part_eda(config, payload, user_id=user_id)
    db.flush()
    return config


def _is_part_eda_conflict(exc: IntegrityError) -> bool:
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None) == "uq_part_eda_part"


def _apply_part_eda(config: PartEda, payload: PartEdaIn, *, user_id: UUID | None) -> None:
    """Write every column from the payload — this is a replacement, so an
    omitted field lands as its default rather than keeping its old value."""
    for field, value in payload.model_dump().items():
        setattr(config, field, value)
    config.updated_by = user_id


def delete_part_eda(db: Session, *, ws: Any, part) -> bool:
    """Drop the part's EDA configuration. Returns whether a row went."""
    config = get_part_eda(db, ws=ws, part=part)
    if config is None:
        return False
    db.delete(config)
    db.flush()
    return True
