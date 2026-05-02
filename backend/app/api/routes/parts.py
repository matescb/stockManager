from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import or_, select

from app.api._helpers import assert_in_workspace, require_resource_access
from app.api.routes._activity import _DEFAULT_LIMIT, _MAX_LIMIT, build_activity
from app.api.routes._parts_shared import (
    get_part as _get_part,
    image_urls_for_parts as _image_urls_for_parts,
    serialize_part as _serialize,
)
from app.core.config import settings
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession, require_role
from app.core.pagination import decode_cursor, paginate
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import Envelope, ok
from app.core.secrets import decrypt
from app.core.time import utcnow
from app.domain.audit.service import log as _audit_log
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import BulkImportIdempotency, Part, PartMetaMember, PartSubstitute
from app.domain.parts.providers import make_provider
from app.domain.parts.services.provider_cache import lookup_fresh, lookup_with_cache
from app.domain.parts.schemas import (
    BulkDeleteIn,
    MetaMemberIn,
    PartIn,
    PartPatch,
    QuickRemoveBagIn,
    ScanImportIn,
    ScanImportRow,
    SubstituteIn,
)
from app.domain.parts.services.bag_signature import compute_bag_signature
from app.domain.parts.services.assets import fetch_provider_asset
from app.domain.stock.models import StockEntry
from app.domain.stock.schemas import AddStockIn, LotInput
from app.domain.stock.service import (
    StockError,
    add_stock,
    bulk_current_quantities,
    reserved_quantity,
    stock_summary_for_part,
    total_for_part,
)
from app.domain.storage.models import StorageLocation
from app.domain.workspaces.models import Workspace

router = APIRouter()
logger = logging.getLogger(__name__)


# PartIn, PartPatch, BulkDeleteIn, ScanImportIn etc. live in
# `app.domain.parts.schemas` (CQ-006). Helper functions
# `_image_urls_for_parts`, `_serialize`, `_get_part` live in
# `_parts_shared` so subsequent split files (parts_assets,
# parts_provider, parts_bulk per #118) can share them without
# duplicating.


# ---------------------------------------------------------------------------
# Provider assets (images + datasheets, downloaded at part-creation /
# refresh time and served from our own origin so the app keeps working
# when the upstream CDN rotates a URL or goes down).
#
# Files live at {UPLOAD_DIR}/parts/{ws_id}/{sha256}.{ext} — content-
# addressed, so the immutable cache header is safe and overwrites can't
# break in-flight requests.
# ---------------------------------------------------------------------------


# MIME-by-extension map for the serve route. Anything not in this set
# is treated as an opaque binary and forced to download as an attachment
# — which keeps a future provider-asset-MIME drift (e.g. an HTML page
# erroneously saved with a .bin extension) from rendering inline. SVG is
# intentionally absent; SEC2-006 / SEC2-011.
_ASSET_MIME_BY_EXT: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
}
_INLINE_EXTS: frozenset[str] = frozenset({"jpg", "jpeg", "png", "gif", "webp"})


@router.get("/assets/{ws_id}/{filename}")
def get_provider_asset(
    ws_id: UUID,
    filename: str,
    ws: CurrentWorkspace,
    name: str | None = Query(default=None, max_length=120),
):
    # Workspace-scoped: an operator can only fetch assets that live under
    # their workspace's folder. The `ws` dep already proves membership in
    # the request's current workspace; this matches them.
    if ws_id != ws.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    # Disallow path traversal — filename must be a flat content-addressed name.
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid filename")

    abs_path = os.path.join(settings().UPLOAD_DIR, "parts", str(ws_id), filename)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    served_mime = _ASSET_MIME_BY_EXT.get(ext, "application/octet-stream")
    # Image MIMEs may stay inline so <img> tags work; everything else
    # (PDFs, opaque binaries) is forced to download to neuter any
    # MIME-confusion path. Mirrors the attachments.py pattern.
    inline = ext in _INLINE_EXTS
    headers = {
        # Content-addressed → safe to cache for a year, never re-revalidate.
        "Cache-Control": "public, max-age=31536000, immutable",
        # Belt-and-braces against a future bug that lets a MIME differ
        # from the served extension.
        "X-Content-Type-Options": "nosniff",
    }
    if name:
        # `inline` keeps image preview working; the filename only comes
        # into play when the user does Save As. Restrict to a safe
        # subset and append the original extension so the saved file
        # opens in the right viewer.
        safe = "".join(c for c in name if c.isalnum() or c in "._-")[:80] or "datasheet"
        ext_suffix = f".{ext}" if ext and not safe.lower().endswith(f".{ext.lower()}") else ""
        disposition_type = "inline" if inline else "attachment"
        headers["Content-Disposition"] = f'{disposition_type}; filename="{safe}{ext_suffix}"'
        return FileResponse(abs_path, media_type=served_mime, headers=headers)

    if inline:
        return FileResponse(abs_path, media_type=served_mime, headers=headers)
    # Non-image, no caller-supplied filename — still force attachment so
    # an `evil.bin` lands as a download rather than a rendered page.
    # Set the header explicitly rather than relying on Starlette's
    # `content_disposition_type` kwarg — that param was added in
    # 0.36+ and silently no-ops on older versions, leaving the response
    # without a Content-Disposition at all.
    headers["Content-Disposition"] = "attachment"
    return FileResponse(abs_path, media_type=served_mime, headers=headers)


@router.get("")
def list_parts(
    db: DbSession,
    ws: CurrentWorkspace,
    q: str | None = Query(default=None),
    archived: bool = Query(default=False),
    mpn: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    cursor: str | None = Query(default=None),
    paged: bool = Query(default=False),
) -> Envelope[Any]:
    """List parts.

    Two response shapes — keyed off the request, NOT the route:

      - Default (no ``cursor``, no ``paged=true``): bare list of parts.
        Preserves the pre-cursor public API so the many lookup-style
        consumers (BOM dropdowns, OrderDetail, ScanImport's MPN dup check,
        …) keep working without per-call migration.

      - Cursor opt-in (``?cursor=…`` OR ``?paged=true``): paged envelope
        ``{items: [...], next_cursor: str | null}``.
        Pass ``?cursor=<next_cursor>`` from the previous response to fetch
        the next page.  ``next_cursor`` is null when no further pages
        exist. The ``cursor`` is an HMAC-signed blob — tampering returns
        400.

    Every query is scoped to the current workspace (CLAUDE.md invariant).
    """
    use_paged = paged or cursor is not None
    decoded_cursor = decode_cursor(cursor) if cursor else None

    stmt = select(Part).where(Part.workspace_id == ws.id)
    stmt = stmt.where(Part.archived_at.is_(None) if not archived else Part.archived_at.is_not(None))
    if mpn:
        stmt = stmt.where(Part.mpn == mpn)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Part.name.ilike(like),
                Part.mpn.ilike(like),
                Part.manufacturer.ilike(like),
                Part.internal_part_number.ilike(like),
                Part.description.ilike(like),
            )
        )

    if use_paged:
        parts, next_cursor = paginate(
            db,
            stmt,
            sort_col=Part.name,
            id_col=Part.id,
            cursor=decoded_cursor,
            limit=limit,
        )
    else:
        # Legacy bare-list shape — keep the same ORDER BY (name, id) the
        # paged path uses so two consumers viewing the same workspace in
        # the same instant agree on row order.
        parts = list(
            db.execute(stmt.order_by(Part.name.asc(), Part.id.asc())).scalars()
        )
        next_cursor = None

    part_ids = [p.id for p in parts]
    image_urls = _image_urls_for_parts(db, ws.id, part_ids)
    on_hand_map = bulk_current_quantities(db, workspace_id=ws.id, part_ids=part_ids, status="on_hand")
    reserved_map = bulk_current_quantities(db, workspace_id=ws.id, part_ids=part_ids, status="reserved")
    items = []
    for p in parts:
        items.append(_serialize(
            p,
            on_hand=on_hand_map.get(p.id, 0),
            reserved=reserved_map.get(p.id, 0),
            image_url=image_urls.get(p.id),
        ))
    if use_paged:
        return ok({"items": items, "next_cursor": next_cursor})
    return ok(items)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_part(
    payload: PartIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser
) -> Envelope[dict]:
    # Name defaults to MPN when blank — paste-an-MPN-and-go workflow.
    # At least one of the two has to be set; the partial unique index on
    # (workspace_id, mpn) enforces no-duplicate-MPN at the DB level, but
    # we pre-check here so the response can name the existing part.
    name = (payload.name or "").strip()
    mpn = (payload.mpn or "").strip()
    if not name and not mpn:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="provide at least one of `name` or `mpn`",
        )
    if not name:
        name = mpn

    if mpn:
        existing = (
            db.query(Part)
            .filter(Part.workspace_id == ws.id, Part.mpn == mpn, Part.archived_at.is_(None))
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": f"MPN already used by part \"{existing.name}\"",
                    "existing_id": str(existing.id),
                    "existing_name": existing.name,
                },
            )

    # default_storage_location_id is caller-supplied; it must point at a
    # storage row in this workspace. Without this guard a caller in
    # workspace B can persist a foreign storage UUID as the default for one
    # of their parts (existence-oracle + foot-gun for downstream lookups).
    if payload.default_storage_location_id is not None:
        assert_in_workspace(
            db, StorageLocation, payload.default_storage_location_id, ws.id,
            label="storage location",
        )

    p = Part(
        workspace_id=ws.id,
        part_type=payload.part_type,
        name=name,
        manufacturer=payload.manufacturer,
        mpn=mpn or None,
        internal_part_number=payload.internal_part_number,
        description=payload.description,
        notes_markdown=payload.notes_markdown,
        footprint=payload.footprint,
        low_stock_report_quantity=payload.low_stock_report_quantity,
        attrition_percentage=payload.attrition_percentage,
        attrition_min_quantity=payload.attrition_min_quantity,
        default_storage_location_id=payload.default_storage_location_id,
        default_storage_mandatory=payload.default_storage_mandatory,
        serialized=payload.serialized,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(p)
    # `get_db` commits on clean route exit (BE2-010). No explicit
    # db.commit() here — a route-local commit would split the
    # transaction boundary and partial state could outlive a later
    # raise.
    db.flush()
    return ok(_serialize(p, on_hand=0, reserved=0))


@router.get("/{part_id}")
def get_part(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    # Read endpoint — the archived part page still loads (so the user
    # can read it, restore it, or check past activity).
    p = _get_part(db, ws.id, part_id, include_archived=True)
    on_hand = total_for_part(db, workspace_id=ws.id, part_id=p.id)
    reserved = reserved_quantity(db, workspace_id=ws.id, part_id=p.id)
    image_url = _image_urls_for_parts(db, ws.id, [p.id]).get(p.id)
    return ok(_serialize(p, on_hand=on_hand, reserved=reserved, image_url=image_url))


@router.patch("/{part_id}")
def patch_part(part_id: UUID, payload: PartPatch, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    # Write — refuse archived parts. Editing a retired part would create
    # a misleading audit trail and is the BE2-016 vector.
    p = _get_part(db, ws.id, part_id)
    data = payload.model_dump(exclude_unset=True)
    unlink = bool(data.pop("unlink_provider", False))

    # Linked-part guards: manufacturer + MPN are provider-owned for as long
    # as the link is active. The user must explicitly unlink to edit them.
    if p.linked_provider and not unlink:
        for f in ("manufacturer", "mpn"):
            if f in data and data[f] != getattr(p, f):
                raise HTTPException(
                    status_code=400,
                    detail=f"{f} is provider-owned on a linked part; pass unlink_provider=true to take ownership",
                )

    # Editing description on a linked part flips the locally-edited flag so
    # subsequent provider refreshes won't overwrite the user's wording.
    if (
        "description" in data
        and p.linked_provider is not None
        and data["description"] != p.description
    ):
        p.description_locally_edited = True

    # default_storage_location_id is caller-supplied via the generic setattr
    # loop below; must validate it before assignment. None clears the FK,
    # which is allowed without lookup.
    if data.get("default_storage_location_id") is not None:
        assert_in_workspace(
            db, StorageLocation, data["default_storage_location_id"], ws.id,
            label="storage location",
        )

    for k, v in data.items():
        setattr(p, k, v)
    p.updated_by = user.id

    if unlink:
        p.linked_provider = None
        p.last_refresh_at = None
        p.description_locally_edited = False
        # Convert every provider/override custom_field on this part into a
        # plain manual row, dropping the saved original.
        rows = list(
            db.execute(
                select(CustomField)
                .where(CustomField.workspace_id == ws.id)
                .where(CustomField.object_type == "part")
                .where(CustomField.object_id == p.id)
                .where(CustomField.source.in_(["provider", "override"]))
            ).scalars()
        )
        for r in rows:
            r.source = "manual"
            r.original_value = None
            r.updated_by = user.id

    return ok(
        _serialize(
            p,
            on_hand=total_for_part(db, workspace_id=ws.id, part_id=p.id),
            reserved=reserved_quantity(db, workspace_id=ws.id, part_id=p.id),
        )
    )


# Archive/restore are admin+ — they're workspace-management ops, not
# regular operational tasks. Members get a 403 if they try (Arch HIGH-3).
# `require_resource_access` enforces resource-existence BEFORE the role
# check, so a non-admin probing a foreign workspace's part_id gets 404
# (not 403). The previous shape used `Depends(require_role("admin"))`
# which fired the role check first and turned the response into a
# membership oracle (BE2-009).
@router.post("/{part_id}/archive")
def archive_part(
    request: Request,
    part_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    from sqlalchemy import func, select as sa_select
    from app.domain.attachments.models import Attachment
    from app.domain.custom_fields.models import CustomField as CF
    from app.domain.tags.models import TagLink

    p = require_resource_access(db, Part, part_id, ws=ws, user=user, role="admin", label="part")
    p.archived_at = utcnow()

    # Observability: log how many polymorphic rows are associated with the
    # archived part so operators can gauge orphan risk without a full scan.
    def _count(Model, ws_id, obj_id):
        return db.execute(
            sa_select(func.count()).select_from(Model).where(
                Model.workspace_id == ws_id,
                Model.object_id == obj_id,
            )
        ).scalar_one()

    logger.info(
        "part archived",
        extra={
            "workspace_id": str(ws.id),
            "part_id": str(p.id),
            "polymorphic_attachments": _count(Attachment, ws.id, p.id),
            "polymorphic_custom_fields": _count(CF, ws.id, p.id),
            "polymorphic_tag_links": _count(TagLink, ws.id, p.id),
        },
    )

    _audit_log(
        db,
        ws=ws,
        user=user,
        action="part.archived",
        target_type="part",
        target_ids=[p.id],
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(None, "archived")


@router.post("/{part_id}/restore")
def restore_part(
    request: Request,
    part_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    p = require_resource_access(db, Part, part_id, ws=ws, user=user, role="admin", label="part")
    p.archived_at = None
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="part.restored",
        target_type="part",
        target_ids=[p.id],
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(None, "restored")


@router.post("/bulk-delete", dependencies=[Depends(require_role("admin"))])
@limiter.limit("30/minute", key_func=workspace_key)
def bulk_delete_parts(
    request: Request,
    payload: BulkDeleteIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Soft-delete (archive) the listed parts in one shot.

    Hard-deleting would foreign-key-cascade into stock_entries / lots /
    order_entries / bom_entries — the user can already filter
    `/parts/archived` to review or restore.

    Workspace-scoped: ids that don't belong to this workspace are not
    visible and land in ``not_found_ids`` (no information leak about
    other workspaces — the shape is the same as for truly missing IDs).

    Response buckets:
    - ``archived_ids``       — IDs that were active and are now archived.
    - ``already_archived_ids`` — IDs that existed in this workspace but
                               were already archived (no-op).
    - ``not_found_ids``      — IDs not found in this workspace (either
                               truly missing OR owned by another workspace
                               — deliberately indistinguishable).
    """
    now = utcnow()
    requested = set(payload.part_ids)

    rows = (
        db.query(Part)
        .filter(Part.workspace_id == ws.id, Part.id.in_(requested))
        .all()
    )
    found_ids = {p.id for p in rows}

    archived_ids = []
    already_archived_ids = []
    for p in rows:
        if p.archived_at is None:
            p.archived_at = now
            archived_ids.append(p.id)
        else:
            already_archived_ids.append(p.id)

    not_found_ids = [pid for pid in requested if pid not in found_ids]

    # Emit one audit row for the whole bulk operation.
    all_touched = archived_ids + already_archived_ids
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="part.bulk_archived",
        target_type="part",
        target_ids=all_touched or None,
        comment=(
            f"not_found={len(not_found_ids)} "
            f"already_archived={len(already_archived_ids)}"
        ) if (not_found_ids or already_archived_ids) else None,
        request_id=getattr(request.state, "request_id", None),
    )

    return ok(
        {
            "archived_ids": [str(i) for i in archived_ids],
            "already_archived_ids": [str(i) for i in already_archived_ids],
            "not_found_ids": [str(i) for i in not_found_ids],
        },
        f"archived {len(archived_ids)}",
    )


_BAG_SIG_RE = re.compile(r"[a-f0-9]{64}")


def _is_valid_bag_signature(s: str) -> bool:
    """Return True iff ``s`` is a 64-char lowercase hex string (SHA-256 digest)."""
    return bool(_BAG_SIG_RE.fullmatch(s))


@router.get("/by-bag-signature/{signature}")
def find_by_bag_signature(signature: str, db: DbSession, ws: CurrentWorkspace):
    """Look up the most recent stock_entry whose bag_signature matches.
    Used by the scan-import UI: when the operator scans the same physical
    bag again, the frontend hits this endpoint to surface the prior
    import inline instead of waiting for the bulk-import POST to come
    back with a `bag_rescan` status."""
    if not _is_valid_bag_signature(signature):
        # SHA-256 hex digest is exactly 64 lower-case hex chars.  Reject
        # anything that doesn't match (old code accepted upper-case via
        # .isalnum(); tightened by BE2-015).
        return ok(None)
    prior = db.execute(
        select(StockEntry)
        .where(StockEntry.workspace_id == ws.id)
        .where(StockEntry.bag_signature == signature)
        .order_by(StockEntry.occurred_at.desc())
        .limit(1)
    ).scalars().first()
    if prior is None:
        return ok(None)
    return ok({
        "part_id": str(prior.part_id),
        "lot_id": str(prior.lot_id) if prior.lot_id else None,
        "storage_location_id": (
            str(prior.storage_location_id) if prior.storage_location_id else None
        ),
        "quantity": int(prior.quantity_delta or 0),
    })


@router.get("/{part_id}/stock")
def part_stock(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id, include_archived=True)
    rows = stock_summary_for_part(db, workspace_id=ws.id, part_id=p.id)
    return ok(
        {
            "total_on_hand": total_for_part(db, workspace_id=ws.id, part_id=p.id),
            "rows": [
                {
                    "storage_location_id": str(r["storage_location_id"]) if r["storage_location_id"] else None,
                    "lot_id": str(r["lot_id"]) if r["lot_id"] else None,
                    "quantity": r["quantity"],
                }
                for r in rows
            ],
        }
    )


@router.get("/{part_id}/lots")
def part_lots(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    from app.domain.lots.models import Lot
    p = _get_part(db, ws.id, part_id, include_archived=True)
    lots = list(
        db.execute(
            select(Lot).where(Lot.workspace_id == ws.id).where(Lot.part_id == p.id).order_by(Lot.created_at.desc())
        ).scalars()
    )
    return ok(
        [
            {
                "id": str(l.id),
                "name": l.name,
                "serial_number": l.serial_number,
                "purchase_quantity": l.purchase_quantity,
                "purchase_unit_cost": float(l.purchase_unit_cost) if l.purchase_unit_cost is not None else None,
                "purchase_currency": l.purchase_currency,
                "expiration_date": l.expiration_date.isoformat() if l.expiration_date else None,
                "comments": l.comments,
                "parent_lot_id": str(l.parent_lot_id) if l.parent_lot_id else None,
                "source_type": l.source_type,
                "created_at": l.created_at.isoformat(),
            }
            for l in lots
        ]
    )


@router.post("/{part_id}/substitutes")
def add_substitute(part_id: UUID, payload: SubstituteIn, db: DbSession, ws: CurrentWorkspace):
    # Both sides must be live parts — `_get_part` defaults to refusing
    # archived rows, which is what BE2-016 wants here. Adding a
    # substitute against an archived part would create a binding that
    # can't ever resolve usefully.
    p = _get_part(db, ws.id, part_id)
    sub = _get_part(db, ws.id, payload.substitute_part_id)
    db.add(PartSubstitute(part_id=p.id, substitute_part_id=sub.id, direction=payload.direction))
    return ok(None)


@router.get("/{part_id}/substitutes")
def list_substitutes(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id, include_archived=True)
    rows = list(db.execute(select(PartSubstitute).where(PartSubstitute.part_id == p.id)).scalars())
    return ok([{"part_id": str(r.substitute_part_id), "direction": r.direction} for r in rows])


@router.delete("/{part_id}/substitutes/{substitute_id}")
def del_substitute(part_id: UUID, substitute_id: UUID, db: DbSession, ws: CurrentWorkspace):
    # Removal allowed even on archived rows — operators should be able
    # to clean up dead bindings.
    p = _get_part(db, ws.id, part_id, include_archived=True)
    db.query(PartSubstitute).filter(
        PartSubstitute.part_id == p.id, PartSubstitute.substitute_part_id == substitute_id
    ).delete()
    return ok(None)


# ---- Meta-part members ----------------------------------------------------


@router.get("/{meta_id}/members")
def list_members(meta_id: UUID, db: DbSession, ws: CurrentWorkspace):
    meta = _get_part(db, ws.id, meta_id, include_archived=True)
    rows = list(
        db.execute(
            select(PartMetaMember).where(PartMetaMember.meta_part_id == meta.id)
        ).scalars()
    )
    return ok([{"id": str(r.id), "member_part_id": str(r.part_id)} for r in rows])


@router.post("/{meta_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(meta_id: UUID, payload: MetaMemberIn, db: DbSession, ws: CurrentWorkspace):
    meta = _get_part(db, ws.id, meta_id)
    if meta.part_type != "meta":
        raise HTTPException(status_code=400, detail="part is not a meta-part")
    member = _get_part(db, ws.id, payload.member_part_id)
    if member.id == meta.id:
        raise HTTPException(status_code=400, detail="meta-part cannot include itself")
    if member.part_type == "meta":
        raise HTTPException(status_code=400, detail="meta-part members cannot themselves be meta")
    existing = (
        db.execute(
            select(PartMetaMember)
            .where(PartMetaMember.meta_part_id == meta.id)
            .where(PartMetaMember.part_id == member.id)
        )
        .scalars()
        .first()
    )
    if existing:
        return ok({"id": str(existing.id), "member_part_id": str(existing.part_id)})
    row = PartMetaMember(meta_part_id=meta.id, part_id=member.id)
    db.add(row)
    db.flush()
    return ok({"id": str(row.id), "member_part_id": str(row.part_id)})


@router.delete("/{meta_id}/members/{member_id}")
def del_member(meta_id: UUID, member_id: UUID, db: DbSession, ws: CurrentWorkspace):
    # Removal — allowed even on archived meta.
    meta = _get_part(db, ws.id, meta_id, include_archived=True)
    db.query(PartMetaMember).filter(
        PartMetaMember.meta_part_id == meta.id, PartMetaMember.part_id == member_id
    ).delete()
    return ok(None, "deleted")


@router.get("/{part_id}/activity")
def part_activity(
    request: Request,
    part_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    before_occurred_at: str | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
):
    # Read endpoint — let archived parts surface their history too.
    p = _get_part(db, ws.id, part_id, include_archived=True)

    # Parse cursor
    cursor_at: datetime | None = None
    if before_occurred_at is not None:
        try:
            cursor_at = datetime.fromisoformat(before_occurred_at)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid before_occurred_at")

    stmt = (
        select(StockEntry)
        .where(StockEntry.workspace_id == ws.id)
        .where(StockEntry.part_id == p.id)
    )
    if cursor_at is not None and before_id is not None:
        from sqlalchemy import or_, and_, tuple_
        stmt = stmt.where(
            or_(
                StockEntry.occurred_at < cursor_at,
                and_(
                    StockEntry.occurred_at == cursor_at,
                    StockEntry.id < before_id,
                ),
            )
        )
    stmt = stmt.order_by(StockEntry.occurred_at.desc(), StockEntry.id.desc()).limit(limit + 1)
    stock_rows = list(db.execute(stmt).scalars())

    # Per-request user cache stashed on request.state so it's isolated per request.
    if not hasattr(request.state, "user_cache"):
        request.state.user_cache = {}

    result = build_activity(
        db,
        stock_rows=stock_rows,
        created_at=p.created_at,
        updated_at=p.updated_at,
        created_by=p.created_by,
        updated_by=p.updated_by,
        created_kind="part_created",
        updated_kind="part_updated",
        limit=limit,
        include_synthetic=(cursor_at is None),
        user_cache=request.state.user_cache,
    )
    return ok(result)


# Reserved keys that surface elsewhere on PartInfo (Media card). These
# are also treated as `source='provider'` rows but we keep them out of
# the spec body when listing.
_PROVIDER_RESERVED_KEYS = ("image_url", "datasheet_url")


@router.post("/{part_id}/refresh-from-provider")
@limiter.limit("60/minute", key_func=workspace_key)
def refresh_from_provider(
    request: Request,
    part_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Re-run the workspace's configured MPN lookup against this part's
    stored MPN. Reconciles `source='provider'` custom_field rows
    (insert / update / delete) and never touches `manual` / `override`.
    Updates manufacturer + mpn + footprint always; description only when
    it hasn't been locally edited."""
    p = _get_part(db, ws.id, part_id)
    if not (p.mpn or "").strip():
        raise HTTPException(status_code=400, detail="part has no MPN to look up")

    provider = make_provider(
        ws.parts_provider,
        decrypt(ws.parts_provider_api_key),
        decrypt(ws.parts_provider_api_secret),
    )
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail="no parts provider configured (set one in Workspace settings)",
        )

    # Use lookup_fresh (not lookup_with_cache) — the operator explicitly
    # triggered a refresh, so we always hit upstream.  The fresh result is
    # written back to the cache so subsequent lookup_with_cache calls see it.
    out = lookup_fresh(provider, p.mpn.strip())
    if not out.get("found") or not out.get("result"):
        return ok(
            {
                "found": False,
                "message": out.get("message") or "no match",
                "provider": provider.name,
            }
        )

    r = out["result"]
    p.manufacturer = r.get("manufacturer") or p.manufacturer
    new_mpn = r.get("mpn") or p.mpn
    if new_mpn:
        p.mpn = new_mpn
    fp = r.get("footprint")
    if fp:
        # On every refresh we let the provider drive footprint — same
        # treatment as manufacturer/mpn (provider-owned for linked parts).
        p.footprint = fp
    if not p.description_locally_edited:
        new_desc = r.get("description")
        if new_desc:
            p.description = new_desc
    p.linked_provider = provider.name
    p.linked_external_id = r.get("mpn") or p.linked_external_id
    p.last_refresh_at = utcnow()
    p.updated_by = user.id

    # Reconcile spec rows. For each provider-supplied (key, value):
    #   • existing row, source='provider'  → update value
    #   • existing row, source='manual'    → leave alone (user owns it)
    #   • existing row, source='override'  → leave alone, but remember the
    #     new upstream value as the new `original_value` so a Restore
    #     reflects current upstream, not historical.
    #   • absent                           → insert with source='provider'
    # After processing, any source='provider' row whose key isn't in the
    # upstream payload (and isn't a reserved system key) is deleted.
    desired: dict[str, str] = {}
    for s in r.get("specs") or []:
        key = (s.get("key") or "").strip()
        value = (s.get("value") or "").strip()
        if key:
            desired[key] = value
    # Download provider assets locally — same fallback semantics as
    # bulk-import: failed downloads keep the upstream URL.
    if r.get("image_url"):
        local = fetch_provider_asset(r["image_url"], str(ws.id), "image")
        desired["image_url"] = local or r["image_url"]
    if r.get("datasheet_url"):
        local = fetch_provider_asset(r["datasheet_url"], str(ws.id), "datasheet")
        desired["datasheet_url"] = local or r["datasheet_url"]

    existing_rows = list(
        db.execute(
            select(CustomField)
            .where(CustomField.workspace_id == ws.id)
            .where(CustomField.object_type == "part")
            .where(CustomField.object_id == p.id)
        ).scalars()
    )
    by_key = {row.key: row for row in existing_rows}

    added = updated = removed = 0
    for key, value in desired.items():
        row = by_key.get(key)
        if row is None:
            db.add(
                CustomField(
                    workspace_id=ws.id,
                    object_type="part",
                    object_id=p.id,
                    key=key,
                    value=value,
                    source="provider",
                    created_by=user.id,
                    updated_by=user.id,
                )
            )
            added += 1
        elif row.source == "provider":
            if row.value != value:
                row.value = value
                row.updated_by = user.id
                updated += 1
        elif row.source == "override":
            # Refresh the saved baseline so the Restore button reverts to
            # the current upstream value — not what was sent the first
            # time the part was linked.
            if row.original_value != value:
                row.original_value = value
                row.updated_by = user.id

    upstream_keys = set(desired.keys())
    for row in existing_rows:
        if row.source == "provider" and row.key not in upstream_keys:
            db.delete(row)
            removed += 1

    return ok(
        {
            "found": True,
            "provider": provider.name,
            "summary": {
                "added": added,
                "updated": updated,
                "removed": removed,
            },
            "part": _serialize(
                p,
                on_hand=total_for_part(db, workspace_id=ws.id, part_id=p.id),
                reserved=reserved_quantity(db, workspace_id=ws.id, part_id=p.id),
            ),
        }
    )


# ---------------------------------------------------------------------------
# Bulk import from a barcode-scan session.
#
# The frontend's scan-import flow accumulates rows of {mpn, quantity?,
# storage_location_id?} as bags are scanned. Each row goes through the
# same MPN→provider→canonical-record pipeline used by lookup-mpn, then
# we materialise: a Part (linked-type), `source='provider'` custom_fields
# for every spec, and — if quantity>0 and a storage location is given —
# a stock entry. The endpoint returns one status row per input so the
# UI can show a per-row outcome banner ("created", "duplicate", "no match").
# ---------------------------------------------------------------------------


_BULK_IMPORT_REQUEST_DEADLINE_S = 60.0   # wall-clock budget for the whole request
_BULK_IMPORT_ROW_TIMEOUT_S = 8.0          # per-row provider-lookup timeout
_BULK_IMPORT_IDEMPOTENCY_TTL_H = 24       # hours before cache rows are swept


def _bulk_import_content_key(ws_id: str, rows) -> str:
    """Derive a deterministic 64-hex-char SHA-256 key from request content.

    Serialises every field of every row (sorted by a stable key) so that
    two calls with different quantities / storage locations / lot names hash
    to different keys even when the MPNs and bag signatures are identical.
    Order-independent: rows are sorted by (bag_signature or "", mpn) before
    serialisation so the operator may re-order rows between retries.
    """
    row_blobs = sorted(
        json.dumps(r.model_dump(), sort_keys=True, default=str)
        for r in rows
    )
    raw = f"{ws_id}|{'||'.join(row_blobs)}"
    return hashlib.sha256(raw.encode()).hexdigest()


@router.post("/bulk-import-from-scan")
@limiter.limit("5/minute", key_func=workspace_key)
def bulk_import_from_scan(
    request: Request,
    payload: ScanImportIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Materialise scanned bag rows into Parts (+ optional initial stock).

    Each row is independent — both at the result-shape level (duplicates /
    no-match outcomes are returned inline rather than aborting the batch)
    AND at the database level: every row that does writes is wrapped in a
    SAVEPOINT (`db.begin_nested()`) so an unexpected exception mid-row
    (IntegrityError on a unique constraint, asset-fetch network error,
    anything we didn't anticipate) rolls back ONLY that row's writes
    without losing the rest of the batch. The outer transaction commits
    every surviving savepoint at the end. Without this, a single uncaught
    exception in row N would discard rows 1..N-1's writes — which the
    operator already saw acknowledged in the per-row outcome list — and
    the audit trail would diverge from what was actually persisted (Sec
    CRIT-6).

    Partial-commit semantics: savepoints commit durably at the outer
    `db.commit()` near the end of this function. If the proxy 502/504s
    *after* that commit, the client did not see the response body — but
    the rows are durably persisted. Retrying with the same `idempotency_key`
    returns the cached envelope verbatim (no new Parts created). Retrying
    *without* an idempotency key will re-derive the same content-hash and
    likewise return the cached result (BE2-003).

    Bounded latency (BE2-003):
    - Row cap: max 50 rows per request (ScanImportIn.rows max_length=50).
    - Request deadline: 60 s total. Rows not reached before the deadline
      are returned with status="deadline_exceeded".
    - Per-row provider timeout: 8 s. A slow MPN surfaces as
      status="lookup_failed" with a timeout reason; neighbouring rows
      still process.
    """
    # ------------------------------------------------------------------
    # Idempotency — best-effort sweep of expired rows then cache lookup.
    #
    # The key is FE-supplied (UUID4 generated once per submit attempt,
    # re-sent unchanged on retry) or falls back to a SHA-256 content
    # hash of the full row payload so that true retries of identical
    # bytes are deduplicated even without an explicit key. The content
    # hash includes all fields (quantity, storage_location_id, etc.) so
    # two calls that differ in any detail are treated as distinct.
    # ------------------------------------------------------------------
    explicit_key = (payload.idempotency_key or "").strip() or None
    idempotency_key = explicit_key or _bulk_import_content_key(str(ws.id), payload.rows)

    # Sweep rows older than TTL (best-effort, don't abort on failure).
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_BULK_IMPORT_IDEMPOTENCY_TTL_H)
        db.execute(
            BulkImportIdempotency.__table__.delete().where(
                BulkImportIdempotency.workspace_id == ws.id,
                BulkImportIdempotency.created_at < cutoff,
            )
        )
    except Exception:
        pass

    # Cache lookup — MUST filter by workspace_id (isolation invariant).
    # Only check the cache when the FE supplied an explicit key. Relying
    # on the content-hash fallback for cache HIT would suppress the
    # duplicate-MPN detection path for a second scan of the same MPN —
    # the client sends identical bytes but expects a live re-check.
    if explicit_key:
        cached = db.execute(
            select(BulkImportIdempotency)
            .where(BulkImportIdempotency.workspace_id == ws.id)
            .where(BulkImportIdempotency.key == idempotency_key)
        ).scalars().first()
        if cached is not None:
            return ok(cached.result_json)

    # ------------------------------------------------------------------
    # Provider setup
    # ------------------------------------------------------------------
    provider = make_provider(
        ws.parts_provider,
        decrypt(ws.parts_provider_api_key),
        decrypt(ws.parts_provider_api_secret),
    )
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail="no parts provider configured (set one in Workspace settings)",
        )

    # ------------------------------------------------------------------
    # Per-request deadline
    # ------------------------------------------------------------------
    deadline = monotonic() + _BULK_IMPORT_REQUEST_DEADLINE_S

    # Function-scope executor for per-row provider timeouts. We deliberately
    # do NOT use `with ThreadPoolExecutor(...) as pool:` at row scope — that
    # calls `shutdown(wait=True)` on exit, which blocks waiting for any
    # timed-out worker thread to finish its hung HTTP call (defeating the
    # bounded-blocking goal). Instead we hold a single executor for the
    # whole request, abandon timed-out futures, and tear down with
    # `wait=False` + `cancel_futures=True` at the end so the request
    # returns even if a worker is still hung. Hung worker threads will
    # finish on the provider's own socket timeout and exit cleanly.
    _bulk_import_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="bulk-import-lookup",
    )

    out_rows: list[dict] = []
    for row in payload.rows:
        # Check wall-clock budget before starting each row.
        if monotonic() >= deadline:
            out_rows.append({
                "mpn": (row.mpn or "").strip() or row.mpn,
                "status": "deadline_exceeded",
                "error": "request deadline exceeded; retry with the same idempotency_key",
            })
            continue

        mpn = (row.mpn or "").strip()
        if not mpn:
            out_rows.append({
                "mpn": row.mpn,
                "status": "invalid",
                "error": "empty MPN",
            })
            continue

        # Per-row workspace validation of caller-supplied storage. Without
        # this, the Part is committed with `default_storage_location_id`
        # pointing at a foreign workspace's row (existence-oracle + downstream
        # foot-gun). Same fix as create_part / patch_part — surface the
        # failure as `invalid` so the rest of the batch still runs.
        if row.storage_location_id is not None:
            sl = db.get(StorageLocation, row.storage_location_id)
            if sl is None or sl.workspace_id != ws.id:
                out_rows.append({
                    "mpn": mpn,
                    "status": "invalid",
                    "error": "storage location not found in workspace",
                })
                continue

        # Server-side bag_signature verification (BE2-015).  When the
        # client supplies the raw bag code alongside the signature we
        # recompute the digest independently.  A mismatch means a buggy
        # or adversarial client — surface it as `bag_signature_mismatch`
        # so the operator sees something and ops aren't blind to the bug.
        if row.bag_signature and row.raw_bag_code is not None:
            expected = compute_bag_signature(row.raw_bag_code)
            if expected != row.bag_signature:
                out_rows.append({
                    "mpn": mpn,
                    "status": "bag_signature_mismatch",
                    "error": "bag_signature does not match recomputed digest of raw_bag_code",
                })
                continue

        # Bag re-scan recognition — same physical bag scanned again.
        # The first import wrote bag_signature on the resulting
        # stock_entry; finding it now means we should offer the operator
        # a path to consume from this lot rather than double-importing.
        if row.bag_signature:
            prior = db.execute(
                select(StockEntry)
                .where(StockEntry.workspace_id == ws.id)
                .where(StockEntry.bag_signature == row.bag_signature)
                .order_by(StockEntry.occurred_at.desc())
                .limit(1)
            ).scalars().first()
            if prior is not None:
                out_rows.append({
                    "mpn": mpn,
                    "status": "bag_rescan",
                    "part_id": str(prior.part_id),
                    "lot_id": str(prior.lot_id) if prior.lot_id else None,
                    "storage_location_id": (
                        str(prior.storage_location_id) if prior.storage_location_id else None
                    ),
                    "quantity": int(prior.quantity_delta or 0),
                })
                continue

        # Duplicate check — workspace-scoped, case-sensitive (mirrors how
        # GET /parts?mpn= matches).
        existing = db.execute(
            select(Part)
            .where(Part.workspace_id == ws.id)
            .where(Part.mpn == mpn)
            .where(Part.archived_at.is_(None))
            .limit(1)
        ).scalars().first()
        if existing is not None:
            out_rows.append({
                "mpn": mpn,
                "status": "duplicate",
                "part_id": str(existing.id),
            })
            continue

        # Provider lookup with per-row timeout. The provider classes are
        # synchronous HTTP; submit to a function-scope ThreadPoolExecutor
        # future so we can enforce a hard timeout without rewriting them.
        #
        # IMPORTANT: the executor is created ONCE for the whole request
        # (see _bulk_import_executor below) and we deliberately do NOT
        # `shutdown(wait=True)` between rows. A `with ThreadPoolExecutor(...)`
        # block at row scope would block on shutdown waiting for the
        # timed-out worker thread to finish its hung HTTP call — which
        # defeats the entire bounded-blocking goal under prod's --workers 1.
        # On timeout we abandon the future (the worker thread leaks for
        # the duration of the provider socket timeout, but the request
        # continues) and surface the row as `lookup_failed`.
        # We capture unexpected exceptions to Sentry as belt-and-braces —
        # the row still resolves with `lookup_failed`.
        row_budget = deadline - monotonic()
        actual_timeout = min(_BULK_IMPORT_ROW_TIMEOUT_S, max(0.5, row_budget))
        lookup: dict | None = None
        try:
            fut = _bulk_import_executor.submit(lookup_with_cache, provider, mpn)
            try:
                lookup = fut.result(timeout=actual_timeout)
            except concurrent.futures.TimeoutError:
                # Cancel if still queued; if running, the worker thread
                # will finish in the background and its result is dropped.
                fut.cancel()
                out_rows.append({
                    "mpn": mpn,
                    "status": "lookup_failed",
                    "error": f"provider timeout after {actual_timeout:.1f}s",
                })
                continue
        except Exception as exc:
            try:  # local import keeps this path zero-cost when SENTRY_DSN is empty
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except Exception:
                pass
            out_rows.append({
                "mpn": mpn,
                "status": "lookup_failed",
                "error": f"provider raised {type(exc).__name__}",
            })
            continue
        if not lookup.get("found") or not lookup.get("result"):
            out_rows.append({
                "mpn": mpn,
                "status": "lookup_failed",
                "error": lookup.get("message") or "no match",
            })
            continue

        r = lookup["result"]
        # Name: description if we have it, else MPN. Both providers
        # typically return a useful description.
        name = (r.get("description") or "").strip() or mpn
        # Truncate to the column limit (Part.name is varchar(300)).
        if len(name) > 300:
            name = name[:300]

        # Wrap every write for this row in a savepoint. If anything
        # below this line raises (IntegrityError on the partial-unique
        # MPN constraint, asset-fetch raising mid-flight, fetch_provider_asset
        # crashing, anything else unanticipated) — only this row rolls
        # back. Other rows in the batch keep their writes.
        try:
            with db.begin_nested():
                p, qty_added, stock_error = _import_one_scan_row(
                    db, ws=ws, user=user, row=row, mpn=mpn,
                    provider_name=provider.name, lookup_result=r,
                )
        except Exception as exc:
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except Exception:
                pass
            out_rows.append({
                "mpn": mpn,
                "status": "row_failed",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        out_rows.append({
            "mpn": mpn,
            "status": "created",
            "part_id": str(p.id),
            "quantity_added": qty_added,
            "stock_error": stock_error,
        })

    # Tear down the executor without waiting for any hung worker thread.
    # `cancel_futures=True` cancels still-queued futures; running ones
    # are abandoned (their result is discarded; thread finishes when its
    # underlying socket times out). This is the critical bit that keeps
    # the per-row timeout bounded: a `with` block at row scope (or a
    # `wait=True` shutdown here) would re-introduce the wall-clock pin.
    _bulk_import_executor.shutdown(wait=False, cancel_futures=True)

    # `bulk_import_from_scan` keeps an explicit terminal commit even
    # though `get_db` commits on clean exit (BE2-010). Savepoint
    # releases aren't independently durable — they only become durable
    # at the OUTER transaction's commit. The real reason is response-
    # build robustness: if anything between this point and the dep's
    # final commit raises (an unexpected serialisation error, a Sentry
    # tag enrich that hits a network blip), we don't want to lose a
    # batch of imports the operator already saw on the scanner. Commit
    # here pins the batch; the dep's commit on clean exit is a no-op.
    summary = {
        "created":                  sum(1 for r in out_rows if r["status"] == "created"),
        "duplicate":                sum(1 for r in out_rows if r["status"] == "duplicate"),
        "bag_rescan":               sum(1 for r in out_rows if r["status"] == "bag_rescan"),
        "bag_signature_mismatch":   sum(1 for r in out_rows if r["status"] == "bag_signature_mismatch"),
        "lookup_failed":            sum(1 for r in out_rows if r["status"] == "lookup_failed"),
        "invalid":                  sum(1 for r in out_rows if r["status"] == "invalid"),
        "row_failed":               sum(1 for r in out_rows if r["status"] == "row_failed"),
        "deadline_exceeded":        sum(1 for r in out_rows if r["status"] == "deadline_exceeded"),
    }
    result_payload = {"rows": out_rows, "summary": summary, "provider": provider.name}

    # Write idempotency cache entry before committing so a concurrent
    # identical request (race window is tiny) sees the result immediately.
    #
    # CRITICAL: this MUST be a true `INSERT … ON CONFLICT DO NOTHING`
    # (postgres dialect upsert), NOT plain ORM `add`/`flush`. On the race
    # path (two concurrent requests with the same key reach this point
    # together) a plain `flush()` would raise `IntegrityError` on the
    # composite-PK conflict — and a Session-level `db.rollback()` here
    # would unwind the OUTER transaction, discarding every per-row
    # savepoint write. The response would still report
    # `summary: {created: N, …}` while ZERO Parts persist — that's the
    # exact partial-commit divergence this PR is supposed to fix.
    # `on_conflict_do_nothing` makes the second writer a silent no-op
    # at the SQL level so the outer tx stays intact.
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    db.execute(
        pg_insert(BulkImportIdempotency.__table__)
        .values(
            workspace_id=ws.id,
            key=idempotency_key,
            result_json=result_payload,
            created_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "key"])
    )

    db.commit()
    return ok(result_payload)


def _import_one_scan_row(
    db,
    *,
    ws,
    user,
    row,
    mpn: str,
    provider_name: str,
    lookup_result: dict,
):
    """Write the Part + provider custom_fields + initial stock for a
    single bulk-import row, INSIDE a caller-managed savepoint. Returns
    (part, qty_added, stock_error). Raises on any unanticipated DB
    failure — the caller's `with db.begin_nested():` rolls back this
    row only.
    """
    r = lookup_result
    name = (r.get("description") or "").strip() or mpn
    if len(name) > 300:
        name = name[:300]

    p = Part(
        workspace_id=ws.id,
        part_type="linked",
        name=name,
        manufacturer=(r.get("manufacturer") or None),
        mpn=(r.get("mpn") or mpn),
        description=(r.get("description") or None),
        footprint=(r.get("footprint") or None),
        attrition_percentage=0,
        attrition_min_quantity=0,
        default_storage_location_id=row.storage_location_id,
        default_storage_mandatory=False,
        serialized=False,
        linked_provider=provider_name,
        linked_external_id=(r.get("mpn") or mpn),
        last_refresh_at=utcnow(),
        description_locally_edited=False,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(p)
    db.flush()  # assign p.id for the custom_fields below

    # Materialise spec rows + image/datasheet as `source='provider'`,
    # mirroring the refresh-from-provider path. Skip empties.
    for s in (r.get("specs") or []):
        key = (s.get("key") or "").strip()
        value = (s.get("value") or "").strip()
        if not key or not value:
            continue
        db.add(CustomField(
            workspace_id=ws.id,
            object_type="part",
            object_id=p.id,
            key=key,
            value=value,
            source="provider",
            created_by=user.id,
            updated_by=user.id,
        ))
    # Download image + datasheet locally so we don't depend on the
    # provider's CDN at render time. Failed downloads fall back to
    # the original URL so the worst case is unchanged from before.
    if r.get("image_url"):
        local = fetch_provider_asset(r["image_url"], str(ws.id), "image")
        db.add(CustomField(
            workspace_id=ws.id,
            object_type="part",
            object_id=p.id,
            key="image_url",
            value=local or r["image_url"],
            source="provider",
            created_by=user.id,
            updated_by=user.id,
        ))
    if r.get("datasheet_url"):
        local = fetch_provider_asset(r["datasheet_url"], str(ws.id), "datasheet")
        db.add(CustomField(
            workspace_id=ws.id,
            object_type="part",
            object_id=p.id,
            key="datasheet_url",
            value=local or r["datasheet_url"],
            source="provider",
            created_by=user.id,
            updated_by=user.id,
        ))

    # Initial stock entry — when the bag's Q field carries a count
    # (or the operator entered one), the part lands on-hand right
    # away. Storage location is optional: when present, the entry is
    # filed there; when absent, it's recorded with no location so the
    # operator can move/file it later from the Stock view.
    qty_added = 0
    stock_error: str | None = None
    if row.quantity and row.quantity > 0:
        lot_payload: LotInput | None = None
        if row.lot_name or row.lot_serial:
            lot_payload = LotInput(
                name=row.lot_name,
                serial_number=row.lot_serial,
            )
        try:
            add_stock(
                db,
                workspace_id=ws.id,
                user_id=user.id,
                payload=AddStockIn(
                    part_id=p.id,
                    quantity=row.quantity,
                    storage_location_id=row.storage_location_id,
                    lot=lot_payload,
                    comments=row.comments,
                    bag_signature=row.bag_signature,
                ),
            )
            qty_added = row.quantity
        except StockError as exc:
            # Don't fail the whole row — the part is created, but surface
            # the stock issue so the UI can flag it. StockError is
            # caught here (inside the savepoint) rather than letting it
            # bubble out, because we don't want a stock-add failure to
            # roll back the Part + provider specs the operator already
            # sees as "created" in the response.
            stock_error = str(exc)

    return p, qty_added, stock_error


@router.post("/{part_id}/quick-remove-bag")
def quick_remove_bag(
    part_id: UUID,
    payload: QuickRemoveBagIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Remove `quantity` units from a specific (lot, location) combo —
    used by the scan-import re-scan UI to consume from a bag that was
    imported earlier without forcing the operator into the full
    Remove-Stock form. remove_stock enforces the lot's actual on-hand
    via the same path as the manual flow, so an over-qty request
    still 4xx's cleanly."""
    p = _get_part(db, ws.id, part_id)
    from app.domain.stock.schemas import RemoveStockIn
    from app.domain.stock.service import remove_stock as _remove
    try:
        _remove(
            db,
            workspace_id=ws.id,
            user_id=user.id,
            payload=RemoveStockIn(
                part_id=p.id,
                quantity=payload.quantity,
                storage_location_id=payload.storage_location_id,
                lot_id=payload.lot_id,
                comments=payload.comments,
            ),
        )
    except StockError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ok(None, "removed")
