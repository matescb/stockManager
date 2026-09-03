"""Core parts CRUD, stock/lots roll-up, substitutes, meta-members, activity.

All endpoints in this module share the /api/parts prefix (registered in
main.py). No URL structure changes from the original monolithic parts.py.
"""
from __future__ import annotations

import logging
import re
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.api._helpers import assert_in_workspace, require_resource_access
from app.api.routes._activity import (
    _DEFAULT_LIMIT,
    _MAX_LIMIT,
    route_activity,
)
from app.api.routes._parts_shared import (
    audit_fields_comment as _audit_fields_comment,
)
from app.api.routes._parts_shared import (
    get_part as _get_part,
)
from app.api.routes._parts_shared import (
    image_urls_for_parts as _image_urls_for_parts,
)
from app.api.routes._parts_shared import (
    provider_links_for as _provider_links_for,
)
from app.api.routes._parts_shared import (
    raise_mpn_conflict as _raise_mpn_conflict,
)
from app.api.routes._parts_shared import (
    serialize_part as _serialize,
)
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession, require_role
from app.core.errors import ErrorCodes, raise_http
from app.core.pagination import decode_cursor, paginate
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import Envelope, ok
from app.core.time import utcnow
from app.domain.audit.service import log as _audit_log
from app.domain.categories.models import PartCategory
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part
from app.domain.parts.provider_links import (
    delete_link as _delete_provider_link,
)
from app.domain.parts.provider_links import (
    get_link as _get_provider_link,
)
from app.domain.parts.schemas import (
    BulkDeleteIn,
    PartIn,
    PartPatch,
)
from app.domain.parts.services.mpn_unique import (
    active_part_by_mpn as _active_part_by_mpn,
)
from app.domain.parts.services.mpn_unique import is_mpn_unique_violation
from app.domain.stock.models import StockEntry
from app.domain.stock.service import (
    bulk_current_quantities,
    reserved_quantity,
    stock_summary_for_part,
    total_for_part,
)
from app.domain.storage.models import StorageLocation

router = APIRouter()
logger = logging.getLogger(__name__)


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
) -> Envelope[object]:
    """List parts.

    Two response shapes — keyed off the request, NOT the route:

      - Default (no ``cursor``, no ``paged=true``): bare list of parts,
        respecting ``limit`` (default 50, max 200). Preserves the
        pre-cursor public API so the many lookup-style consumers (BOM
        dropdowns, OrderDetail, ScanImport's MPN dup check, …) keep
        working without per-call migration. Callers that need more than
        the default 50 must pass ``?limit=N`` explicitly (capped at 200).
        For workspaces larger than 200 parts use the cursor-paged path.

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
        # the same instant agree on row order. ``limit`` is honoured here
        # too (issue #286) — previously it was silently ignored on this
        # branch and a workspace with 50k parts would ship the entire
        # catalog over the wire.
        parts = list(
            db.execute(
                stmt.order_by(Part.name.asc(), Part.id.asc()).limit(limit)
            ).scalars()
        )
        next_cursor = None

    part_ids = [p.id for p in parts]
    image_urls = _image_urls_for_parts(db, ws.id, part_ids)
    on_hand_map = bulk_current_quantities(
        db, workspace_id=ws.id, part_ids=part_ids, status="on_hand"
    )
    reserved_map = bulk_current_quantities(
        db, workspace_id=ws.id, part_ids=part_ids, status="reserved"
    )
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
    request: Request,
    payload: PartIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[dict]:
    # Name defaults to MPN when blank — paste-an-MPN-and-go workflow.
    # At least one of the two has to be set; the partial unique index on
    # (workspace_id, mpn) enforces no-duplicate-MPN at the DB level, but
    # we pre-check here so the response can name the existing part.
    name = (payload.name or "").strip()
    mpn = (payload.mpn or "").strip()
    if not name and not mpn:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCodes.PART_NAME_OR_MPN_REQUIRED,
            message="provide at least one of `name` or `mpn`",
        )
    if not name:
        name = mpn

    if mpn:
        existing = _active_part_by_mpn(db, workspace_id=ws.id, mpn=mpn)
        if existing:
            _raise_mpn_conflict(existing)

    # default_storage_location_id is caller-supplied; it must point at a
    # storage row in this workspace. Without this guard a caller in
    # workspace B can persist a foreign storage UUID as the default for one
    # of their parts (existence-oracle + foot-gun for downstream lookups).
    if payload.default_storage_location_id is not None:
        assert_in_workspace(
            db, StorageLocation, payload.default_storage_location_id, ws.id,
            label="storage location",
        )

    # Same guard for the caller-supplied category — a foreign category_id
    # would otherwise persist as a cross-workspace FK. Archived categories
    # are hidden from every picker, so accepting one here could only come
    # from a stale or hand-crafted request.
    if payload.category_id is not None:
        category = assert_in_workspace(
            db, PartCategory, payload.category_id, ws.id, label="category",
        )
        if category.archived_at is not None:
            raise_http(409, ErrorCodes.CATEGORY_ARCHIVED, "Category is archived")

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
        category_id=payload.category_id,
        created_by=user.id,
        updated_by=user.id,
    )
    try:
        with db.begin_nested():
            db.add(p)
            # `get_db` commits on clean route exit (BE2-010). No explicit
            # db.commit() here — a route-local commit would split the
            # transaction boundary and partial state could outlive a later
            # raise.
            db.flush()
    except IntegrityError as exc:
        if mpn and is_mpn_unique_violation(exc):
            existing = _active_part_by_mpn(db, workspace_id=ws.id, mpn=mpn)
            if existing is not None:
                _raise_mpn_conflict(existing)
        raise
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="part.created",
        target_type="part",
        target_ids=[p.id],
        comment=_audit_fields_comment(set(payload.model_fields_set) | {"name"}),
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(_serialize(p, on_hand=0, reserved=0))


@router.get("/{part_id}")
def get_part(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    # Read endpoint — the archived part page still loads (so the user
    # can read it, restore it, or check past activity).
    p = _get_part(db, ws.id, part_id, include_archived=True)
    on_hand = total_for_part(db, workspace_id=ws.id, part_id=p.id)
    reserved = reserved_quantity(db, workspace_id=ws.id, part_id=p.id)
    image_url = _image_urls_for_parts(db, ws.id, [p.id]).get(p.id)
    return ok(
        _serialize(
            p,
            on_hand=on_hand,
            reserved=reserved,
            image_url=image_url,
            provider_links=_provider_links_for(db, ws.id, p.id),
        )
    )


@router.patch("/{part_id}")
def patch_part(
    request: Request,
    part_id: UUID,
    payload: PartPatch,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
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
                raise_http(
                    400,
                    code=ErrorCodes.PART_LINKED_PROVIDER_OWNED_FIELD,
                    message=(
                        f"{f} is provider-owned on a linked part; "
                        "pass unlink_provider=true to take ownership"
                    ),
                    field=f,
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

    # Same as above — an explicit null clears the category and needs no
    # lookup; a UUID must resolve inside this workspace. Archived is only
    # rejected when the value CHANGES: a part already pointing at a
    # since-archived category must stay patchable (the settings form
    # round-trips the current value).
    if data.get("category_id") is not None:
        category = assert_in_workspace(
            db, PartCategory, data["category_id"], ws.id, label="category"
        )
        if category.archived_at is not None and data["category_id"] != p.category_id:
            raise_http(409, ErrorCodes.CATEGORY_ARCHIVED, "Category is archived")

    for k, v in data.items():
        setattr(p, k, v)
    p.updated_by = user.id

    if unlink:
        # The link row for the provider being released has to go with it,
        # or the part keeps advertising a link whose part columns are now
        # locally owned. Secondary links are untouched.
        if p.linked_provider:
            primary_link = _get_provider_link(
                db, workspace_id=ws.id, part_id=p.id, provider=p.linked_provider
            )
            if primary_link is not None:
                _delete_provider_link(db, primary_link)
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

    _audit_log(
        db,
        ws=ws,
        user=user,
        action="part.updated",
        target_type="part",
        target_ids=[p.id],
        comment=_audit_fields_comment(set(payload.model_fields_set)),
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(
        _serialize(
            p,
            on_hand=total_for_part(db, workspace_id=ws.id, part_id=p.id),
            reserved=reserved_quantity(db, workspace_id=ws.id, part_id=p.id),
            provider_links=_provider_links_for(db, ws.id, p.id),
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
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.domain.attachments.models import Attachment
    from app.domain.custom_fields.models import CustomField as CF
    from app.domain.tags.models import TagLink

    p = require_resource_access(db, Part, part_id, ws=ws, user=user, role="admin", label="part")
    reserved = total_for_part(db, workspace_id=ws.id, part_id=p.id, status="reserved")
    if reserved > 0:
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.PART_HAS_RESERVED_STOCK,
            message="part has reserved stock; release reservations first",
            blocking=[{"part_id": str(p.id), "quantity": reserved}],
        )

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
                    "storage_location_id": (
                        str(r["storage_location_id"])
                        if r["storage_location_id"]
                        else None
                    ),
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
            select(Lot)
            .where(Lot.workspace_id == ws.id)
            .where(Lot.part_id == p.id)
            .order_by(Lot.created_at.desc())
        ).scalars()
    )
    return ok(
        [
            {
                "id": str(lot.id),
                "name": lot.name,
                "serial_number": lot.serial_number,
                "purchase_quantity": lot.purchase_quantity,
                "purchase_unit_cost": (
                    float(lot.purchase_unit_cost)
                    if lot.purchase_unit_cost is not None
                    else None
                ),
                "purchase_currency": lot.purchase_currency,
                "expiration_date": lot.expiration_date.isoformat()
                if lot.expiration_date
                else None,
                "comments": lot.comments,
                "parent_lot_id": str(lot.parent_lot_id) if lot.parent_lot_id else None,
                "source_type": lot.source_type,
                "created_at": lot.created_at.isoformat(),
            }
            for lot in lots
        ]
    )

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

    stmt = (
        select(StockEntry)
        .where(StockEntry.workspace_id == ws.id)
        .where(StockEntry.part_id == p.id)
    )
    return ok(route_activity(
        request,
        db,
        stmt,
        before_occurred_at=before_occurred_at,
        before_id=before_id,
        limit=limit,
        entity=p,
        created_kind="part_created",
        updated_kind="part_updated",
    ))
