"""Turning an `ImportPlan` into library rows, blobs and part wiring.

`vendor_zip.py` and `lcsc.py` decide *what* an archive or an LCSC part
offers; this module is the only place that writes it down. Both feed the
same `ImportPlan`, so a fix to the storage or wiring rules lands on both
import paths at once.

Three rules carried over from the P2 upload routes, and the reason each
exists:

* **Digest first, store last.** A row is inserted before its blob is
  written, so a rejected entry (409, 404) can't leave an orphan file
  behind (P2 security review MEDIUM-2).
* **Name conflicts suffix, they don't fail.** A single-file upload
  answers 409 and lets the user rename. An archive can't — refusing the
  whole zip because one of six files shares a name with something
  already hosted would be useless — so a colliding name takes a
  ` (2)`-style suffix, up to ` (9)`, and only then surfaces the 409.
* **A bad member is a note, not an error.** Anything that fails
  validation is reported in `skipped` and the rest of the archive still
  imports.

Footprint model paths are rewritten to `${STOCKMGR_3D}/<row name>`
*before* the footprint is canonicalised and stored, because the stored
bytes are what phase 6 packages. A `(model …)` naming a file the archive
didn't carry is dropped rather than left dangling — KiCad reports a
missing model on every board that places the footprint otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NamedTuple
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.core.errors import ErrorCodes
from app.domain.eda import kicad_refs, sexpr, storage
from app.domain.eda import service as eda_service
from app.domain.eda.models import EdaDatafile, EdaFootprint, EdaSymbol, PartEda
from app.domain.eda.vendor_zip import ImportPlan, PendingDatafile, PendingEntry, Skipped

__all__ = [
    "MODEL_PATH_VAR",
    "CreatedRow",
    "ImportResult",
    "import_plan",
    "wire_part",
]

# Re-exported from `kicad_refs`, which owns every name this app and
# KiCad have to agree on. Kept as a module-level name here because the
# importer's call sites and tests already read it from this module.
MODEL_PATH_VAR = kicad_refs.MODEL_PATH_VAR

# How far the ` (2)`…` (9)` conflict-suffix walk goes before the 409 the
# single-file upload would have raised is allowed through. Past nine
# collisions the user has a naming problem the importer can't paper over.
_MAX_SUFFIX = 9

# Matches the `String(200)` name columns.
_NAME_MAX = 200

_MODEL_KINDS = ("step", "wrl")

SKIP_INVALID_ENTRY = "entry failed validation"
SKIP_MODELS_UNMATCHED = (
    "3D model paths left unchanged — the archive carried none of the files "
    "the footprint references"
)
SKIP_MODEL_DROPPED = "3D model reference dropped — the archive did not carry this file"


@dataclass(frozen=True)
class CreatedRow:
    id: UUID
    name: str
    created: bool
    sha256: str
    kind: str | None = None


@dataclass
class ImportResult:
    vendor: str
    symbols: list[CreatedRow] = field(default_factory=list)
    footprints: list[CreatedRow] = field(default_factory=list)
    datafiles: list[CreatedRow] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    part_eda_updated: bool = False

    @property
    def rows(self) -> list[CreatedRow]:
        return [*self.symbols, *self.footprints, *self.datafiles]

    @property
    def created_count(self) -> int:
        return sum(1 for row in self.rows if row.created)

    @property
    def reused_count(self) -> int:
        return sum(1 for row in self.rows if not row.created)


# ---------------------------------------------------------------------
# Name conflicts
# ---------------------------------------------------------------------


def _is_name_conflict(exc: HTTPException) -> bool:
    detail = exc.detail
    return (
        exc.status_code == status.HTTP_409_CONFLICT
        and isinstance(detail, dict)
        and detail.get("code") == ErrorCodes.EDA_NAME_CONFLICT
    )


def _candidate_names(name: str, *, keep_extension: bool) -> list[str]:
    """`name`, then `name (2)` … `name (9)`, each fitting the column.

    `keep_extension` puts the suffix BEFORE the final dot, so a data file
    called `P.step` becomes `P (2).step` and not `P.step (2)`. KiCad
    picks its 3D plugin by extension, so the second form loads nothing —
    and the name is what a rewritten `(model …)` path points at, which
    makes a silent failure on the board (P3 code review MED).

    Symbol and footprint names are KiCad ENTRY names, not filenames, so
    they take the suffix at the end even when they contain a dot.
    """
    stem, dot, ext = name.rpartition(".")
    if not (keep_extension and dot and stem):
        return [name[:_NAME_MAX]] + [
            name[: _NAME_MAX - len(f" ({n})")] + f" ({n})"
            for n in range(2, _MAX_SUFFIX + 1)
        ]
    tail = f".{ext}"
    return [name[:_NAME_MAX]] + [
        stem[: _NAME_MAX - len(f" ({n})") - len(tail)] + f" ({n})" + tail
        for n in range(2, _MAX_SUFFIX + 1)
    ]


def _store_entry(
    db,
    *,
    ws,
    Model: type,
    user_id: UUID | None,
    name: str,
    render,
    storage_kind: str,
    datafile_kind: str | None = None,
    category_id: UUID | None = None,
    source: str,
) -> tuple[Any, bool] | None:
    """Insert the row (suffixing on conflict), then write the blob.

    `render(candidate) -> bytes` produces the bytes for a given name, and
    is re-run for EVERY candidate. That is the whole point: a symbol or
    footprint carries its name INSIDE the file, so a suffix that renamed
    only the row would leave `MYPART (2)` pointing at bytes that still
    say `(symbol "MYPART")`. Phase 5 resolves `LibNick:Entry` references
    against the file content, and `service._rewrite_stored_entry_name`
    exists to hold exactly this invariant on the rename path (P3 code
    review HIGH-1).

    Returns None when `render` rejects the entry — an unreadable or
    over-large entry is one skipped file, not a failed archive.
    """
    def insert(candidate: str, data: bytes) -> tuple[Any, bool]:
        sha, size = storage.digest(data)
        row, created = eda_service.upload_entry(
            db,
            ws=ws,
            Model=Model,
            user_id=user_id,
            name=candidate,
            sha256=sha,
            size_bytes=size,
            kind=datafile_kind,
            category_id=category_id,
            source=source,
        )
        storage.store(ws.id, data, kind=storage_kind)
        return row, created

    def rendered(candidate: str) -> bytes | None:
        try:
            return render(candidate)
        except HTTPException:
            return None

    # Split the last candidate out rather than testing an index inside
    # the loop: on the last one a conflict IS the answer, and this way
    # the function has no unreachable tail.
    *earlier, final = _candidate_names(name, keep_extension=Model is EdaDatafile)
    for candidate in earlier:
        data = rendered(candidate)
        if data is None:
            return None
        try:
            return insert(candidate, data)
        except HTTPException as exc:
            if _is_name_conflict(exc):
                continue
            raise
    data = rendered(final)
    if data is None:
        return None
    return insert(final, data)


# ---------------------------------------------------------------------
# Importing a plan
# ---------------------------------------------------------------------


def import_plan(
    db,
    *,
    ws,
    user_id: UUID | None,
    plan: ImportPlan,
    source: str,
    category_id: UUID | None = None,
) -> ImportResult:
    """Create every row the plan describes. Caller owns the transaction.

    Data files land first: a footprint's `(model …)` paths are rewritten
    to point at the rows they produce, and the row names aren't known
    until the conflict-suffix walk has run.
    """
    # Validated once, up front. `upload_entry` also checks it, but only
    # symbols and footprints carry a category — a datafile-only archive
    # would never reach that check and a foreign category_id would come
    # back 200 instead of 404 (P3 isolation review).
    eda_service.assert_category(db, ws=ws, category_id=category_id, changed=True)

    result = ImportResult(vendor=plan.vendor, skipped=list(plan.skipped))

    # Keyed by folded stem and holding a LIST, because `ABC.step` and
    # `ABC.wrl` are the same model in two formats: one `(model …)` path
    # naming either of them should attach both rows to the footprint.
    models_by_stem: dict[str, list[CreatedRow]] = {}
    for pending in plan.datafiles:
        row = _import_datafile(db, ws=ws, user_id=user_id, pending=pending, source=source)
        if row is None:
            result.skipped.append(Skipped(filename=pending.name, reason=SKIP_INVALID_ENTRY))
            continue
        result.datafiles.append(row)
        if pending.kind in _MODEL_KINDS:
            models_by_stem.setdefault(_fold(pending.stem), []).append(row)

    for pending in plan.footprints:
        _import_footprint(
            db,
            ws=ws,
            user_id=user_id,
            pending=pending,
            source=source,
            category_id=category_id,
            models_by_stem=models_by_stem,
            result=result,
        )

    for pending in plan.symbols:
        row = _import_entry(
            db,
            ws=ws,
            Model=EdaSymbol,
            user_id=user_id,
            pending=pending,
            storage_kind=storage.SYMBOL_KIND,
            source=source,
            category_id=category_id,
            node=pending.node,
        )
        if row is None:
            result.skipped.append(Skipped(filename=pending.filename, reason=SKIP_INVALID_ENTRY))
        else:
            result.symbols.append(row)

    return result


def _fold(text: str) -> str:
    """Match model-path stems the way a human would read them — vendors
    disagree about case and about `-` vs `_` in the same file's name."""
    return "".join(c for c in text.lower() if c.isalnum())


def _import_datafile(
    db, *, ws, user_id, pending: PendingDatafile, source: str
) -> CreatedRow | None:
    # A 3D or SPICE file is stored verbatim and carries no name inside
    # it, so every candidate renders the same bytes.
    stored = _store_entry(
        db,
        ws=ws,
        Model=EdaDatafile,
        user_id=user_id,
        name=pending.name,
        render=lambda _candidate: storage.validated_datafile(pending.kind, pending.data),
        storage_kind=pending.kind,
        datafile_kind=pending.kind,
        source=source,
    )
    if stored is None:
        return None
    row, created = stored
    return CreatedRow(
        id=row.id, name=row.name, created=created, sha256=row.sha256, kind=row.kind
    )


def _import_entry(
    db,
    *,
    ws,
    Model: type,
    user_id,
    pending: PendingEntry,
    storage_kind: str,
    source: str,
    category_id: UUID | None,
    node: sexpr.Node,
) -> CreatedRow | None:
    def render(candidate: str) -> bytes:
        # Rename the NODE, not just the row: the entry name lives inside
        # the file and the two must never disagree.
        renamed = node if candidate == pending.name else sexpr.rename(node, candidate)
        return storage.canonical_entry_bytes(renamed, kind=storage_kind, name=candidate)

    stored = _store_entry(
        db,
        ws=ws,
        Model=Model,
        user_id=user_id,
        name=pending.name,
        render=render,
        storage_kind=storage_kind,
        category_id=category_id,
        source=source,
    )
    if stored is None:
        return None
    row, created = stored
    return CreatedRow(id=row.id, name=row.name, created=created, sha256=row.sha256)


def _import_footprint(
    db,
    *,
    ws,
    user_id,
    pending: PendingEntry,
    source: str,
    category_id: UUID | None,
    models_by_stem: dict[str, list[CreatedRow]],
    result: ImportResult,
) -> None:
    node, linked, unmatched_only, dropped = _rewrite_models(pending.node, models_by_stem)
    row = _import_entry(
        db,
        ws=ws,
        Model=EdaFootprint,
        user_id=user_id,
        pending=pending,
        storage_kind=storage.FOOTPRINT_KIND,
        source=source,
        category_id=category_id,
        node=node,
    )
    if row is None:
        result.skipped.append(Skipped(filename=pending.filename, reason=SKIP_INVALID_ENTRY))
        return
    result.footprints.append(row)
    if unmatched_only:
        result.skipped.append(
            Skipped(filename=pending.filename, reason=SKIP_MODELS_UNMATCHED)
        )
    # A PARTIAL drop is the quiet one: some paths resolved, so the
    # footprint imports and looks fine while a model the user expected is
    # simply gone from the board. Name each one (P3 code review MED).
    for path in dropped:
        result.skipped.append(Skipped(filename=path, reason=SKIP_MODEL_DROPPED))
    _link_models(db, ws=ws, user_id=user_id, footprint_id=row.id, models=linked)


def _rewrite_models(
    node: sexpr.Node, models_by_stem: dict[str, list[CreatedRow]]
) -> tuple[sexpr.Node, list[CreatedRow], bool, list[str]]:
    """Point the footprint's `(model …)` nodes at our own storage.

    Returns the rewritten node, the rows it now references in file order,
    whether the footprint referenced models but matched NONE of them —
    in which case the paths are left exactly as the vendor wrote them,
    because rewriting to nothing would be strictly worse than a path the
    user might still resolve locally — and the paths that were dropped
    because their file wasn't in the archive.
    """
    referenced = sexpr.model_paths(node)
    if not referenced:
        return node, [], False, []

    linked: list[CreatedRow] = []
    for path in referenced:
        for row in models_by_stem.get(_fold(_path_stem(path)), ()):
            if row not in linked:
                linked.append(row)
    if not linked:
        return node, [], True, []
    dropped = [
        path for path in referenced if not models_by_stem.get(_fold(_path_stem(path)))
    ]

    def resolve(path: str) -> str | None:
        group = models_by_stem.get(_fold(_path_stem(path)))
        if not group:
            return None
        # Point the path at the STEP when the archive carried both — it's
        # the format the mechanical side actually wants.
        best = min(group, key=lambda row: 0 if row.kind == "step" else 1)
        return f"{MODEL_PATH_VAR}/{best.name}"

    return sexpr.rewrite_model_paths(node, resolve), linked, False, dropped


def _path_stem(path: str) -> str:
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0] if "." in base else base


def _link_models(db, *, ws, user_id, footprint_id: UUID, models: list[CreatedRow]) -> None:
    """Attach the 3D rows the footprint references, STEP first.

    Ordering is the one place the two 3D formats aren't interchangeable:
    KiCad renders the first model, and a STEP is what a mechanical
    engineer wants out of the board.
    """
    ordered = sorted(models, key=lambda row: 0 if row.kind == "step" else 1)
    for position, row in enumerate(ordered):
        eda_service.link_footprint_model(
            db,
            ws=ws,
            footprint_id=footprint_id,
            datafile_id=row.id,
            position=position,
            user_id=user_id,
        )


# ---------------------------------------------------------------------
# Wiring the part
# ---------------------------------------------------------------------


def wire_part(
    db,
    *,
    ws,
    part,
    user_id: UUID | None,
    result: ImportResult,
    overwrite: bool,
) -> bool:
    """Point the part's EDA config at what was just imported.

    Fills EMPTY slots only unless `overwrite` — an import is additive by
    default, because the common case is adding a 3D model to a part
    someone already configured by hand. Nothing else on the config is
    touched: `value`, `keywords` and the exclusion flags are the user's,
    and no vendor archive knows better.

    A slot naming an external `LibNick:Entry` counts as occupied.
    """
    slots = _Slots(
        symbol=result.symbols[0] if result.symbols else None,
        footprint=result.footprints[0] if result.footprints else None,
        spice=next((row for row in result.datafiles if row.kind == "spice"), None),
    )
    if not slots.any():
        return False

    config = eda_service.get_part_eda(db, ws=ws, part=part)
    if config is not None:
        if not _apply(config, slots, overwrite=overwrite):
            return False
        config.updated_by = user_id
        db.flush()
        return True

    # No config yet. Build the row DETACHED and attach it only if
    # something actually gets filled — an import with nothing to wire
    # shouldn't leave an empty configuration behind.
    fresh = PartEda(workspace_id=ws.id, part_id=part.id, created_by=user_id)
    if not _apply(fresh, slots, overwrite=overwrite):
        return False
    fresh.updated_by = user_id
    try:
        with db.begin_nested():
            db.add(fresh)
            db.flush()
    except IntegrityError as exc:
        # Lost race on `uq_part_eda_part`: a concurrent import or save for
        # the same part got its INSERT in first. Recover onto the row that
        # won rather than surfacing a 500 — the same recovery
        # `service.upsert_part_eda` makes for the PUT path.
        if not _is_part_eda_conflict(exc):
            raise
        winner = eda_service.get_part_eda(db, ws=ws, part=part)
        if winner is None:
            raise
        if not _apply(winner, slots, overwrite=overwrite):
            return False
        winner.updated_by = user_id
        db.flush()
    return True


class _Slots(NamedTuple):
    """The three rows an import can wire, any of them absent."""

    symbol: CreatedRow | None
    footprint: CreatedRow | None
    spice: CreatedRow | None

    def any(self) -> bool:
        return any(row is not None for row in self)


def _apply(config: PartEda, slots: _Slots, *, overwrite: bool) -> bool:
    """Fill what this config will take. Returns whether anything moved."""
    changed = False
    if slots.symbol is not None and _fill(
        config, "symbol_id", slots.symbol.id, overwrite, "symbol_ref_external"
    ):
        changed = True
    if slots.footprint is not None and _fill(
        config, "footprint_id", slots.footprint.id, overwrite, "footprint_ref_external"
    ):
        changed = True
    if slots.spice is not None and _fill(
        config, "spice_datafile_id", slots.spice.id, overwrite
    ):
        changed = True
    return changed


def _is_part_eda_conflict(exc: IntegrityError) -> bool:
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None) == "uq_part_eda_part"


def _fill(
    config, slot: str, value: UUID, overwrite: bool, external_slot: str | None = None
) -> bool:
    occupied = getattr(config, slot) is not None or (
        external_slot is not None and getattr(config, external_slot) is not None
    )
    if occupied and not overwrite:
        return False
    if getattr(config, slot) == value and (
        external_slot is None or getattr(config, external_slot) is None
    ):
        return False
    setattr(config, slot, value)
    if external_slot is not None:
        # The CHECK constraint forbids holding both halves of a slot, so
        # taking the hosted side has to clear the external one.
        setattr(config, external_slot, None)
    return True
